"""Table parsers: ``TableParser``, ``DelimTxtParser``, ``ExcelParser``, ``MatParser``.

Each parser wraps polars (DelimTxtParser, ExcelParser) or scipy (MatParser) file parsers
and turns a source (local path or ``http(s)://`` URL) → :class:`polars.LazyFrame` for one
file-format family, keyed by a ``kind`` discriminator (``"txt"`` / ``"excel"`` / ``"mat"``).
A parser carries a :class:`~bdf.table_normalizers.TableNormalizer` field (default empty): its
:meth:`read` returns the normalized frame, and a MAT parser sources its variable names
from that normalizer. A blank normalizer degrades to a raw mechanics-only read.

Polars is licensed under MIT: https://github.com/pola-rs/polars/blob/main/LICENSE
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .file_utils import read_head, resolve_source
from .table_normalizers import TableNormalizer

# ---------------------------------------------------------------------------
# Polars docstring helper
# ---------------------------------------------------------------------------


def _polars_param_desc(func: Any, param: str) -> str:
    """Extract first-paragraph description of ``param`` from ``func``'s docstring.

    Args:
        func: Callable object with a docstring (e.g. a polars function).
        param: Parameter name to extract description for.

    Returns:
        First-paragraph description of the parameter, or empty string if not found.
    """
    doc = inspect.getdoc(func) or ""
    lines = doc.splitlines()
    in_params = False
    in_target = False
    desc: list[str] = []
    for line in lines:
        if line == "Parameters":
            in_params = True
            continue
        if not in_params:
            continue
        if line.startswith("---"):
            continue
        if line == "":
            if in_target and desc:
                break
            continue
        if not line.startswith(" "):
            if in_target:
                break
            name = line.split(":")[0].strip()
            in_target = bool(name) and name == param
        elif in_target:
            desc.append(line.strip())
    return " ".join(desc)


# ---------------------------------------------------------------------------
# TableParser
# ---------------------------------------------------------------------------


class TableParser(BaseModel):
    """Abstract base for all BDF table parsers.

    Concrete subclasses define :attr:`base_exts` and optionally configure
    :attr:`unique_exts` (extensions handled exclusively by this parser variant).
    Every parser carries a :attr:`normalizer` (default empty); :meth:`read`
    returns ``self.normalizer.normalize(...)`` so reading and column mapping
    live on one object. An empty normalizer degrades to a raw read.
    """

    model_config = ConfigDict(frozen=True)

    normalizer: TableNormalizer = TableNormalizer()

    base_exts: ClassVar[frozenset[str]]
    unique_exts: frozenset[str] = frozenset()
    magic_bytes: ClassVar[frozenset[bytes]] = frozenset()
    is_text: ClassVar[bool] = False

    def matches_ext(self, ext: str) -> bool:
        """Return True if ``ext`` (case-insensitive) is handled by this parser.

        Args:
            ext: File extension including dot (e.g. '.csv').

        Returns:
            True if the extension is in base_exts or unique_exts.
        """
        return ext.lower() in (type(self).base_exts | self.unique_exts)

    def matches_magic_bytes(self, head: bytes) -> bool:
        """Return True if ``head`` starts with one of this parser's magic-byte signatures.

        Args:
            head: Head bytes read from the start of a file.

        Returns:
            True if head starts with any declared magic-bytes prefix.
        """
        return any(head.startswith(prefix) for prefix in type(self).magic_bytes)

    def normalizer_score(self, path: str | Path) -> int:
        """Return the normalizer score for ``path``'s column headers, or 0 on any exception.

        Args:
            path: Local file path or URL to score.

        Returns:
            Normalizer score (number of matching columns), or 0 if scoring fails.
        """
        try:
            return self.normalizer.score_columns(self.read_column_headings(resolve_source(path)))
        except Exception:
            return 0

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Read ``path`` to a LazyFrame via the parser's mechanics (no normalization).

        Args:
            path: Local file path or URL to read.

        Returns:
            Raw polars LazyFrame with source column names.

        Raises:
            NotImplementedError: In base class; subclasses must override.
        """
        raise NotImplementedError

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Return the raw column header names from ``path`` without reading data rows.

        Args:
            path: Local file path or URL to read.

        Returns:
            List of source column header names.

        Raises:
            NotImplementedError: In base class; subclasses must override.
        """
        raise NotImplementedError

    def read(
        self,
        path: str | Path,
        *,
        normalize: bool = True,
        validate: bool = True,
        include_optional: bool = True,
        extra_columns: dict[str, str] | None = None,
        lazy: bool = True,
        tz: str = "UTC",
    ) -> pl.LazyFrame | pl.DataFrame:
        """Read ``path`` (local or URL) and return the normalized or raw frame.

        When ``normalize=False``, returns ``self._read_raw(path)`` unchanged.
        An empty :attr:`normalizer` (the default) degrades to a raw read.
        ``validate`` defaults to True: column names are checked against the BDF ontology.
        ``lazy`` defaults to True: the parser's ``LazyFrame`` is returned; pass
        ``lazy=False`` to collect it to a ``pl.DataFrame`` before returning.

        Args:
            path: Local file path or http(s) URL.
            normalize: Apply column normalization when True.
            validate: Validate column names against BDF ontology when True (default).
            include_optional: Include optional BDF columns in output.
            extra_columns: Additional column rename mappings.
            lazy: Return a LazyFrame when True (default); collect to a DataFrame when False.
            tz: IANA timezone applied to naive ``unix_time_second`` datetime formats.
                Defaults to ``"UTC"``; see ``TableNormalizer.normalize``.

        Returns:
            Normalized or raw polars LazyFrame (``lazy=True``) or DataFrame (``lazy=False``).
        """
        resolved = resolve_source(path)
        if not normalize:
            raw = self._read_raw(resolved)
            return raw if lazy else raw.collect()
        lf = self._read_raw(resolved)
        result = self.normalizer.normalize(
            lf,
            include_optional=include_optional,
            extra_columns=extra_columns,
            validate=validate,
            tz=tz,
        )
        assert isinstance(result, pl.LazyFrame)
        return result if lazy else result.collect()


# ---------------------------------------------------------------------------
# DelimTxtParser
# ---------------------------------------------------------------------------


class DelimTxtParser(TableParser):
    """Wraps :func:`polars.scan_csv` for delimited text files (.csv/.tsv/.txt/.dat).

    Adds auto-detection and encoding handling on top of polars' CSV parser.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["txt"] = "txt"
    separator: str | None = Field(
        default=None,
        description=_polars_param_desc(pl.scan_csv, "separator"),
    )
    skip_rows: int | None = Field(
        default=None,
        description=_polars_param_desc(pl.scan_csv, "skip_rows"),
    )
    has_header: bool = Field(
        default=True,
        description=_polars_param_desc(pl.scan_csv, "has_header"),
    )
    decimal_comma: bool | None = Field(
        default=None,
        description=_polars_param_desc(pl.scan_csv, "decimal_comma"),
    )
    truncate_ragged_lines: bool = Field(
        default=False,
        description=_polars_param_desc(pl.scan_csv, "truncate_ragged_lines"),
    )
    encoding: str = Field(
        default="utf-8",
        description=(
            'Python codec name for the file\'s character encoding (e.g. "utf-8", "latin-1", "cp1252"). '
            "Used only to decode head bytes for column name extraction; polars always receives utf8-lossy "
            "for data reading. Invalid codec names surface as LookupError at read time."
        ),
    )

    base_exts: ClassVar[frozenset[str]] = frozenset({".csv", ".txt", ".tsv", ".dat"})
    is_text: ClassVar[bool] = True

    def matches_magic_bytes(self, head: bytes) -> bool:
        """Return True if ``head`` plausibly decodes as text.

        DelimTxtParser has no binary signature, so it can't rely on the prefix
        check other parsers use. Rejects head bytes containing a NUL byte (no
        supported text format embeds one) or where ``errors="replace"``
        decoding yields 1% or more replacement characters (binary garbage).

        Args:
            head: Head bytes read from the start of a file.

        Returns:
            True if head bytes look like text, False if they look binary.
        """
        if b"\x00" in head:
            return False
        decoded = head.decode("utf-8", errors="replace")
        if not decoded:
            return False
        return decoded.count("�") / len(decoded) < 0.01

    @staticmethod
    def _decode_head(head: bytes, encoding: str = "utf-8") -> str:
        """Decode head bytes to text, dropping any trailing partial line.

        Args:
            head: Head bytes from the file.
            encoding: Character encoding to use for decoding.

        Returns:
            Decoded text with trailing partial line removed.
        """
        text = head.decode(encoding, errors="replace")
        last_nl = text.rfind("\n")
        if last_nl >= 0:
            text = text[:last_nl]
        return text

    @model_validator(mode="after")
    def _require_header(self) -> "DelimTxtParser":
        """Reject construction with ``has_header=False``.

        Returns:
            ``self`` unchanged when ``has_header`` is True.

        Raises:
            ValueError: If ``has_header`` is False.
        """
        if not self.has_header:
            raise ValueError(
                "Reading data with bdf requires a header row to map columns to the bdf standard. "
                "If your data has no headers, read directly with polars.scan_csv(..., has_header=False) "
                "then normalize with the TableNormalizer.normalize() method."
            )
        return self

    @staticmethod
    def _detect_structure(sample: str, min_run: int = 15) -> tuple[int, str]:
        """Jointly detect field separator and preamble skip-row count from a text sample.

        For each candidate separator (``,``, ``\\t``, ``;``, ``|``, space):

        1. Split every line on the separator and build a per-line type
            *signature*: the tuple of per-field classes (``"numeric"`` vs.
            ``"str"``), for example ("str", "str", "numeric", "numeric").
            Lines splitting into fewer than 2 fields signature to``None``.
        2. Scan signatures for a *data run*: a maximal block of consecutive
            identical, majority-numeric signatures. Identical signatures mean
            stable column count and types.
        3. Accept the run only if it is at least ``min_run`` long and is
            immediately preceded (``i >= 1``) by a *header* line whose signature
            has the same field count and is majority-``str``. This header check
            rejects structured preamble (e.g. space-delimited metadata) that
            happens to form a numeric run but lacks a string header row above it.
        4. Record ``(skiprows, sep, run_len)`` where ``skiprows = i - 1`` points
            at the header line.

        Across all candidates, pick the one maximizing ``(skiprows, run_len)``:
        prefer the deepest valid header (more preamble consumed), breaking ties
            by the longest data run.

        Args:
            sample: Text sample from the file head.
            min_run: Minimum consecutive data rows with identical type signatures required
                to accept a candidate separator and skip-row count.

        Returns:
            Tuple of ``(skiprows, separator)`` where ``skiprows`` is the number of preamble
            lines before the header row.
        """

        def _classify(field: str) -> str:
            f = field.strip()
            try:
                float(f)
                return "numeric"
            except ValueError:
                return "str"

        def _majority_numeric(sig: tuple[str, ...]) -> bool:
            n = sum(1 for t in sig if t == "numeric")
            return n > len(sig) - n

        def _majority_str(sig: tuple[str, ...]) -> bool:
            n = sum(1 for t in sig if t == "str")
            return n > len(sig) - n

        lines = sample.splitlines()
        candidates: list[tuple[int, str, int]] = []  # (skiprows, sep, run_len)

        for sep in (",", "\t", ";", "|", " "):
            sigs: list[tuple[str, ...] | None] = []
            for line in lines:
                fields = line.rstrip(sep).split(sep)
                sigs.append(tuple(_classify(f) for f in fields) if len(fields) >= 2 else None)

            i = 0
            while i < len(sigs):
                sig = sigs[i]
                if sig is None or not _majority_numeric(sig):
                    i += 1
                    continue
                j = i + 1
                while j < len(sigs) and sigs[j] == sig:
                    j += 1
                run_len = j - i
                if run_len >= min_run and i >= 1:
                    header_sig = sigs[i - 1]
                    if header_sig is not None and len(header_sig) == len(sig) and _majority_str(header_sig):
                        candidates.append((i - 1, sep, run_len))
                i = j

        if not candidates:
            return (0, ",")
        best = max(candidates, key=lambda c: (c[0], c[2]))
        return (best[0], best[1])

    @staticmethod
    def _sniff_decimal(df: pl.DataFrame | pl.LazyFrame) -> bool:
        """Return True if comma-decimal strings dominate string columns, else False.

        Args:
            df: DataFrame to inspect for decimal separator usage.

        Returns:
            True if comma-decimal format is more common than dot-decimal.
        """
        sample = df.head(1000).collect() if isinstance(df, pl.LazyFrame) else df.head(1000)
        comma = dot = 0
        for col in sample.columns:
            if sample[col].dtype in (pl.String, pl.Utf8):
                comma += int(sample[col].str.count_matches(r"\d+,\d+").sum())
                dot += int(sample[col].str.count_matches(r"\d+\.\d+").sum())
        return comma > dot

    @staticmethod
    def _coerce_decimal(lf: pl.LazyFrame, decimal_comma: bool) -> pl.LazyFrame:
        """Replace comma decimal separator with dot in string columns.

        Args:
            lf: Polars LazyFrame to process.
            decimal_comma: If True, replace comma with dot in string columns.

        Returns:
            LazyFrame with decimal separator coerced if decimal_comma is True.
        """
        if not decimal_comma:
            return lf
        schema = lf.collect_schema()
        exprs = [
            pl.col(c).str.replace_all(",", ".", literal=True).alias(c) if dtype in (pl.String, pl.Utf8) else pl.col(c)
            for c, dtype in schema.items()
        ]
        return lf.select(exprs)

    def preamble(self, head: bytes) -> list[str]:
        """Return the preamble (skipped) lines decoded from ``head`` bytes.

        Args:
            head: Head bytes from the file.

        Returns:
            List of preamble lines that will be skipped during parsing.
        """
        sample = self._decode_head(head, self.encoding)
        _skip, _ = self._detect_structure(sample)
        skip = self.skip_rows if self.skip_rows is not None else _skip
        return sample.splitlines()[:skip]

    @staticmethod
    def _build_rename_map(raw: bytes, encoding: str, skip: int, sep: str) -> dict[str, str]:
        """Map mangled (utf8-lossy) column names to properly-decoded names.

        Decodes the header line at ``raw[skip]`` twice: once with ``encoding``
        (proper names) and once with ``utf-8/errors=replace`` (mangled names, matching
        what polars utf8-lossy produces). Returns ``{mangled: proper}`` for columns
        where the two differ.  Returns an empty dict when all names are identical
        (e.g. ASCII-only headers or ``skip`` beyond the buffered content).

        Args:
            raw: Raw bytes from the file head.
            encoding: Proper character encoding for the file.
            skip: Number of lines to skip before the header row.
            sep: Field separator character.

        Returns:
            Dictionary mapping mangled column names to properly-decoded names.
        """
        try:
            proper_cols = DelimTxtParser._decode_head(raw, encoding).splitlines()[skip].split(sep)
            mangled_cols = DelimTxtParser._decode_head(raw, "utf-8").splitlines()[skip].split(sep)
        except IndexError:
            return {}
        return {m: p for m, p in zip(mangled_cols, proper_cols, strict=False) if m != p}

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Parse ``path`` (local) to a LazyFrame, honouring (and auto-sniffing) config.

        Args:
            path: Local file path to read.

        Returns:
            Polars LazyFrame with raw column names and data.
        """
        raw = read_head(path)
        sample = self._decode_head(raw, self.encoding)
        _skip, _sep = self._detect_structure(sample)
        sep = self.separator if self.separator is not None else _sep
        skip = self.skip_rows if self.skip_rows is not None else _skip
        is_utf8 = self.encoding.lower() in ("utf-8", "utf8")
        encoding_arg: Literal["utf8", "utf8-lossy"] = "utf8" if is_utf8 else "utf8-lossy"
        lf = pl.scan_csv(
            Path(path),
            skip_rows=skip,
            separator=sep,
            has_header=self.has_header,
            infer_schema=False,
            encoding=encoding_arg,
            truncate_ragged_lines=self.truncate_ragged_lines,
        )
        if not is_utf8:
            rename_map = self._build_rename_map(raw, self.encoding, skip, sep)
            if rename_map:
                lf = lf.rename(rename_map)
        decimal_comma = self.decimal_comma if self.decimal_comma is not None else self._sniff_decimal(lf)
        return self._coerce_decimal(lf, decimal_comma)

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Return column headers by reading the head bytes of ``path`` (local or URL).

        Args:
            path: Local file path or URL to read.

        Returns:
            List of column header names.
        """
        raw = read_head(path)
        sample = self._decode_head(raw, self.encoding)
        _skip, _sep = self._detect_structure(sample)
        sep = self.separator if self.separator is not None else _sep
        skip = self.skip_rows if self.skip_rows is not None else _skip
        lines = sample.splitlines()
        if skip >= len(lines):
            return []
        return lines[skip].split(sep)


# ---------------------------------------------------------------------------
# ExcelParser
# ---------------------------------------------------------------------------


class ExcelParser(TableParser):
    """Wraps :func:`polars.read_excel` for .xlsx/.xlsm/.xls files.

    Delegates to polars' Excel parser with configurable engines (calamine, openpyxl, xlsx2csv).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["excel"] = "excel"
    engine: Literal["calamine", "openpyxl", "xlsx2csv"] = Field(
        default="calamine",
        description=_polars_param_desc(pl.read_excel, "engine"),
    )
    sheet_id: int | None = Field(
        default=None,
        description=_polars_param_desc(pl.read_excel, "sheet_id"),
    )
    sheet_name: str | None = Field(
        default=None,
        description=_polars_param_desc(pl.read_excel, "sheet_name"),
    )
    sheet_pattern: str | None = Field(
        default=None,
        description=(
            "Case-insensitive regex matched against the workbook's sheet names; the first "
            "matching sheet is read. For vendors whose data-sheet name varies per export "
            "(e.g. Arbin's Channel_1-002 / Channel-6_1 / Channel_6_1). Mutually exclusive "
            "with sheet_id/sheet_name."
        ),
    )
    has_header: bool = Field(
        default=True,
        description=_polars_param_desc(pl.read_excel, "has_header"),
    )
    columns: list[int] | list[str] | str | None = Field(
        default=None,
        description=_polars_param_desc(pl.read_excel, "columns"),
    )
    drop_empty_rows: bool = Field(
        default=True,
        description=_polars_param_desc(pl.read_excel, "drop_empty_rows"),
    )
    read_options: dict[str, Any] | None = Field(
        default=None,
        description=_polars_param_desc(pl.read_excel, "read_options"),
    )

    base_exts: ClassVar[frozenset[str]] = frozenset({".xlsx", ".xlsm", ".xls"})
    magic_bytes: ClassVar[frozenset[bytes]] = frozenset({b"PK\x03\x04"})
    is_text: ClassVar[bool] = False

    @model_validator(mode="after")
    def _require_header(self) -> "ExcelParser":
        """Reject construction with ``has_header=False``.

        Returns:
            ``self`` unchanged when ``has_header`` is True.

        Raises:
            ValueError: If ``has_header`` is False.
        """
        if not self.has_header:
            raise ValueError(
                "Reading data with bdf requires a header row to map columns to the bdf standard. "
                "If your data has no headers, read directly with polars.read_excel(..., has_header=False) "
                "then normalize with the TableNormalizer.normalize() method."
            )
        if self.sheet_pattern is not None and (self.sheet_id is not None or self.sheet_name is not None):
            raise ValueError("sheet_pattern is mutually exclusive with sheet_id/sheet_name")
        return self

    def _resolve_sheet_name(self, path: Path) -> str:
        """Resolve ``sheet_pattern`` to a concrete sheet name for ``path``.

        Args:
            path: Local file path whose sheet names are enumerated.

        Returns:
            The first sheet name matching ``sheet_pattern`` (case-insensitive).

        Raises:
            ValueError: If no sheet matches the pattern.
        """
        import fastexcel

        assert self.sheet_pattern is not None  # guarded by the single caller
        sheets = fastexcel.read_excel(path).sheet_names
        pattern = re.compile(self.sheet_pattern, re.IGNORECASE)
        matches = [name for name in sheets if pattern.search(name)]
        if not matches:
            raise ValueError(f"no sheet matching {self.sheet_pattern!r} in {path.name!r}; sheets: {sheets}")
        if len(matches) > 1:
            # e.g. a multi-channel Arbin export in one workbook: silently reading the
            # first channel would drop the others' data. Mirror the multi-sheet error
            # in _read_sheet and make the caller choose.
            raise ValueError(
                f"multiple sheets match {self.sheet_pattern!r} in {path.name!r}: {matches}; "
                "specify `sheet_name` or `sheet_id` to disambiguate."
            )
        return matches[0]

    def _read_sheet(self, path: str | Path, *, read_options: dict[str, Any], **extra: Any) -> pl.DataFrame:
        """Run ``pl.read_excel`` with the reader's sheet/column config and assert a single sheet.

        Args:
            path: Local file path to read.
            read_options: Polars read_options dict for pl.read_excel.
            **extra: Additional keyword arguments to pass to pl.read_excel.

        Returns:
            Polars DataFrame from the specified sheet.

        Raises:
            ValueError: If the file contains multiple sheets and none is specified.
        """
        kwargs: dict[str, Any] = {"engine": self.engine, "has_header": self.has_header, **extra}
        if self.sheet_id is not None:
            kwargs["sheet_id"] = self.sheet_id
        if self.sheet_name is not None:
            kwargs["sheet_name"] = self.sheet_name
        if self.sheet_pattern is not None:
            kwargs["sheet_name"] = self._resolve_sheet_name(Path(path))
        if self.columns is not None:
            kwargs["columns"] = self.columns
        if read_options:
            kwargs["read_options"] = read_options
        df = pl.read_excel(path, **kwargs)
        if isinstance(df, dict):
            raise ValueError("ExcelParser expects a single sheet; specify `sheet_id` or `sheet_name` to disambiguate.")
        return df

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Parse the configured sheet of ``path`` (local) to a LazyFrame.

        Args:
            path: Local file path to read.

        Returns:
            Polars LazyFrame with raw column names and data.
        """
        df = self._read_sheet(
            Path(path),
            read_options=dict(self.read_options or {}),
            drop_empty_rows=self.drop_empty_rows,
        )
        return df.with_columns(pl.all().cast(pl.Utf8, strict=False)).lazy()

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Return the header row without reading data rows (n_rows=0).

        Args:
            path: Local file path to read.

        Returns:
            List of column header names from the specified sheet.
        """
        return self._read_sheet(resolve_source(path), read_options={**(self.read_options or {}), "n_rows": 0}).columns


# ---------------------------------------------------------------------------
# ParquetParser
# ---------------------------------------------------------------------------


class ParquetParser(TableParser):
    """Wraps :func:`polars.scan_parquet` for .parquet / .pq files."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["parquet"] = "parquet"

    base_exts: ClassVar[frozenset[str]] = frozenset({".parquet", ".pq"})
    magic_bytes: ClassVar[frozenset[bytes]] = frozenset({b"PAR1"})

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Read parquet file to a LazyFrame.

        Args:
            path: Local file path or URL to parquet file.

        Returns:
            A polars LazyFrame containing the parquet data.
        """
        return pl.scan_parquet(str(path))

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Extract column names from parquet file.

        Args:
            path: Local file path or URL to parquet file.

        Returns:
            List of column names.
        """
        return pl.scan_parquet(str(path)).collect_schema().names()


# ---------------------------------------------------------------------------
# JsonParser
# ---------------------------------------------------------------------------


class JsonParser(TableParser):
    """Wraps json load and :func:`polars.from_dict`

    :func:`polars.read_json` can ONLY read records-oriented json
    :func:`polars.LazyFrame(dict)` works both records-oriented and list-oriented
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["json"] = "json"

    base_exts: ClassVar[frozenset[str]] = frozenset({".json"})
    is_text: ClassVar[bool] = True

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Read json file to a LazyFrame. Cannot be truly lazy.

        Args:
            path: Local file path or URL to json file.

        Returns:
            A polars LazyFrame containing the json data.
        """
        import json

        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
        return pl.LazyFrame(data)

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Extract column names from json file.

        Args:
            path: Local file path or URL to json file.

        Returns:
            List of column names.
        """
        import json

        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
        return pl.LazyFrame(data).collect_schema().names()


# ---------------------------------------------------------------------------
# NdjsonParser
# ---------------------------------------------------------------------------


class NdjsonParser(TableParser):
    """Wraps :func:`polars.scan_ndjson` for .ndjson files."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["ndjson"] = "ndjson"

    base_exts: ClassVar[frozenset[str]] = frozenset({".ndjson"})
    is_text: ClassVar[bool] = True

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Read ndjson file to a LazyFrame.

        Args:
            path: Local file path or URL to ndjson file.

        Returns:
            A polars LazyFrame containing the ndjson data.
        """
        return pl.scan_ndjson(path)

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Extract column names from ndjson file.

        Args:
            path: Local file path or URL to ndjson file.

        Returns:
            List of column names.
        """
        return pl.scan_ndjson(path).collect_schema().names()


# ---------------------------------------------------------------------------
# IpcParser
# ---------------------------------------------------------------------------


class IpcParser(TableParser):
    """Wraps :func:`polars.scan_ipc` for .ipc/.arrow/.feather files."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["ipc"] = "ipc"

    base_exts: ClassVar[frozenset[str]] = frozenset({".ipc", ".arrow", ".feather", ".ftr"})
    magic_bytes: ClassVar[frozenset[bytes]] = frozenset({b"ARROW1"})
    is_text: ClassVar[bool] = False

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Read IPC file to a LazyFrame.

        Args:
            path: Local file path or URL to IPC file.

        Returns:
            A polars LazyFrame containing the IPC data.
        """
        return pl.scan_ipc(path)

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Extract column names from IPC file.

        Args:
            path: Local file path or URL to IPC file.

        Returns:
            List of column names.
        """
        return pl.scan_ipc(path).collect_schema().names()


# ---------------------------------------------------------------------------
# NDAParser
# ---------------------------------------------------------------------------


class NDAParser(TableParser):
    """Wraps fastnda for Neware .nda / .ndax binary files."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["nda"] = "nda"

    base_exts: ClassVar[frozenset[str]] = frozenset({".nda", ".ndax"})
    magic_bytes: ClassVar[frozenset[bytes]] = frozenset({b"NEWARE"})

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Read Neware NDA file to a LazyFrame using fastnda.

        Args:
            path: Local file path or URL to .nda or .ndax file.

        Returns:
            A polars LazyFrame containing the NDA data.

        Raises:
            RuntimeError: If fastnda is not installed.
        """
        try:
            import fastnda  # type: ignore
        except ImportError as exc:
            raise RuntimeError("NDAParser requires fastnda. Install with `pip install fastnda`.") from exc
        resolved = resolve_source(path)
        df = fastnda.read(str(resolved))
        return df.lazy()

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Extract column names from Neware NDA file.

        Args:
            path: Local file path or URL to .nda or .ndax file.

        Returns:
            List of column names.
        """
        return self._read_raw(path).collect_schema().names()


# ---------------------------------------------------------------------------
# MatParser
# ---------------------------------------------------------------------------


class MatParser(TableParser):
    """Wraps :func:`scipy.io.loadmat` for .mat (MATLAB) files.

    Converts loaded variables into polars LazyFrames. Variable names to load are
    supplied per call (by the resolved normalizer), keeping the reader free of vendor data.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["mat"] = "mat"

    base_exts: ClassVar[frozenset[str]] = frozenset({".mat"})
    magic_bytes: ClassVar[frozenset[bytes]] = frozenset({b"MATLAB 5.0 MAT-file", b"\x89HDF\r\n\x1a\n"})
    is_text: ClassVar[bool] = False

    def _load(self, path: Path, var_names: list[str]) -> dict[str, Any]:
        """Load variables from a MATLAB .mat file using scipy.io.loadmat.

        Args:
            path: Local file path to the .mat file.
            var_names: List of variable names to load.

        Returns:
            Dictionary of loaded variables.

        Raises:
            RuntimeError: If scipy is not installed.
        """
        try:
            from scipy.io import loadmat
        except ImportError as exc:
            raise RuntimeError("MatParser requires scipy. Install with `pip install scipy`.") from exc
        return loadmat(str(path), variable_names=var_names, squeeze_me=True)

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Load the variables named by :attr:`normalizer` from the .mat file into a LazyFrame.

        Variable names come from ``self.normalizer.known_header_names()`` (a .mat
        file has no header row).

        Args:
            path: Local file path to the .mat file.

        Returns:
            Polars LazyFrame with one column per loaded variable, cast to float64.

        Raises:
            ValueError: If a named variable is missing from the file, or is not 1-D after
                squeezing.
        """
        import numpy as np

        var_names = self.normalizer.known_header_names()
        mat = self._load(Path(path), var_names)

        data: dict[str, Any] = {}
        for name in var_names:
            if name not in mat:
                raise ValueError(f"MatParser: variable {name!r} not found in {path}")
            arr = np.atleast_1d(np.asarray(mat[name]).squeeze())
            if arr.ndim != 1:
                raise ValueError(f"MatParser: variable {name!r} has shape {arr.shape} after squeeze; must be 1-D")
            data[name] = arr.astype(np.float64)
        return pl.DataFrame(data).lazy()

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Return the subset of ``self.normalizer`` source headers present in the .mat file.

        Variable names are sourced from :attr:`normalizer` (a .mat file has no header row).

        Args:
            path: Local file path to the .mat file.

        Returns:
            List of normalizer-known variable names that are present in the file.
        """
        var_names = self.normalizer.known_header_names()
        mat = self._load(Path(path), var_names)
        return [name for name in var_names if name in mat]
