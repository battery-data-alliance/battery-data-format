"""Table parsers: ``TableParser``, ``DelimTxtParser``, ``ExcelParser``, ``MatParser``.

Each parser wraps polars (DelimTxtParser, ExcelParser) or scipy (MatParser) file parsers
and turns a source (local path or ``http(s)://`` URL) → :class:`polars.LazyFrame` for one
file-format family, keyed by a ``kind`` discriminator (``"txt"`` / ``"excel"`` / ``"mat"``).
A parser carries a :class:`~bdf.normalizers.TableNormalizer` field (default empty): its
:meth:`read` returns the normalized frame, and a MAT parser sources its variable names
from that normalizer. A blank normalizer degrades to a raw mechanics-only read.

Polars is licensed under MIT: https://github.com/pola-rs/polars/blob/main/LICENSE
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlparse

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .head_utils import is_url, read_head
from .normalizers import TableNormalizer

# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------


def _ext_from_url(url: str) -> str:
    """Return the file extension from a URL by walking path segments right-to-left."""
    path = urlparse(url).path
    for segment in reversed([s for s in path.split("/") if s]):
        suffix = Path(segment).suffix
        if suffix:
            return suffix.lower()
    raise ValueError(f"no extension found in URL: {url!r}")


# ---------------------------------------------------------------------------
# Polars docstring helper
# ---------------------------------------------------------------------------


def _polars_param_desc(func: Any, param: str) -> str:
    """Extract first-paragraph description of ``param`` from ``func``'s docstring."""
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

    def matches_ext(self, ext: str) -> bool:
        """Return True if ``ext`` (case-insensitive) is handled by this parser."""
        return ext.lower() in (type(self).base_exts | self.unique_exts)

    def normalizer_score(self, path: str | Path) -> int:
        """Return the normalizer score for ``path``'s column headers, or 0 on any exception."""
        try:
            return self.normalizer.score_columns(self.read_column_headings(path))
        except Exception:
            return 0

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Read ``path`` to a LazyFrame via the parser's mechanics (no normalization)."""
        raise NotImplementedError

    def read(
        self,
        path: str | Path,
        *,
        normalize: bool = True,
        include_optional: bool = True,
        extra_columns: dict[str, str] | None = None,
    ) -> pl.LazyFrame:
        """Read ``path`` (local or URL) and return the normalized or raw LazyFrame.

        When ``normalize=False``, returns ``self._read_raw(path)`` unchanged.
        An empty :attr:`normalizer` (the default) degrades to a raw read.
        """
        if not normalize:
            return self._read_raw(path)
        lf = self._read_raw(path)
        result = self.normalizer.normalize(lf, include_optional=include_optional, extra_columns=extra_columns)
        assert isinstance(result, pl.LazyFrame)
        return result


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

    @staticmethod
    def _decode_head(head: bytes, encoding: str = "utf-8") -> str:
        """Decode head bytes to text, dropping any trailing partial line."""
        text = head.decode(encoding, errors="replace")
        last_nl = text.rfind("\n")
        if last_nl >= 0:
            text = text[:last_nl]
        return text

    @model_validator(mode="after")
    def _require_header(self) -> "DelimTxtParser":
        if not self.has_header:
            raise ValueError(
                "Reading data with bdf requires a header row to map columns to the bdf standard. "
                "If your data has no headers, read directly with polars.scan_csv(..., has_header=False) "
                "then normalize with the TableNormalizer.normalize() method."
            )
        return self

    @staticmethod
    def _numeric_ratio(line: str, sep: str) -> float:
        """Fraction of fields in ``line`` (split on ``sep``) that parse as floats."""
        fields = line.split(sep)
        if not fields:
            return 0.0
        hits = 0
        for f in fields:
            try:
                float(f.strip())
                hits += 1
            except ValueError:
                pass
        return hits / len(fields)

    @staticmethod
    def _best_run(lines: list[str], sep: str) -> tuple[int, int, int]:
        """Return (start_idx, run_len, field_count) of the longest run of consecutive
        lines that split on ``sep`` into an equal field count >= 2."""
        field_counts = [n if (n := len(line.rstrip(sep).split(sep))) >= 2 else 0 for line in lines]
        best_start = best_len = best_fc = 0
        i = 0
        while i < len(field_counts):
            if field_counts[i] == 0:
                i += 1
                continue
            fc = field_counts[i]
            j = i
            while j < len(field_counts) and field_counts[j] == fc:
                j += 1
            run_len = j - i
            if run_len * fc > best_len * best_fc or (run_len * fc == best_len * best_fc and run_len > best_len):
                best_start, best_len, best_fc = i, run_len, fc
            i = j
        return best_start, best_len, best_fc

    @staticmethod
    def _detect_separator(sample: str, candidates: tuple[str, ...] = (",", "\t", ";", "|", " ")) -> str:
        """Detect the field separator: the candidate giving the longest consistent
        run of equal field counts, breaking ties by data-row numeric ratio."""
        lines = sample.splitlines()
        best_sep = ","
        best_score = 0
        best_ratio = -1.0
        for sep in candidates:
            start, run_len, fc = DelimTxtParser._best_run(lines, sep)
            score = run_len * fc
            if score == 0:
                continue
            data_idx = start + 1
            ratio = DelimTxtParser._numeric_ratio(lines[data_idx], sep) if data_idx < len(lines) else 0.0
            if score > best_score or (score == best_score and ratio > best_ratio):
                best_sep, best_score, best_ratio = sep, score, ratio
        return best_sep

    @staticmethod
    def _detect_skiprows(sample: str, min_run: int = 5, sep: str | None = None) -> int:
        """Return the number of preamble lines to skip (== the header-line index).

        Locates the start of the longest consistent delimited run on ``sep``
        (auto-detected when None). Falls back to 0 when no run of at least
        ``min_run`` lines exists.
        """
        lines = sample.splitlines()
        if sep is None:
            sep = DelimTxtParser._detect_separator(sample)
        start, run_len, _ = DelimTxtParser._best_run(lines, sep)
        return start if run_len >= min_run else 0

    @staticmethod
    def _sniff_decimal(df: pl.DataFrame | pl.LazyFrame) -> bool:
        """Return True if comma-decimal strings dominate string columns, else False."""
        sample = df.head(1000).collect() if isinstance(df, pl.LazyFrame) else df.head(1000)
        comma = dot = 0
        for col in sample.columns:
            if sample[col].dtype in (pl.String, pl.Utf8):
                comma += int(sample[col].str.count_matches(r"\d+,\d+").sum())
                dot += int(sample[col].str.count_matches(r"\d+\.\d+").sum())
        return comma > dot

    @staticmethod
    def _coerce_decimal(lf: pl.LazyFrame, decimal_comma: bool) -> pl.LazyFrame:
        """Replace comma decimal separator with dot in string columns."""
        if not decimal_comma:
            return lf
        schema = lf.collect_schema()
        exprs = [
            pl.col(c).str.replace_all(",", ".", literal=True).alias(c) if dtype in (pl.String, pl.Utf8) else pl.col(c)
            for c, dtype in schema.items()
        ]
        return lf.select(exprs)

    def preamble(self, head: bytes) -> list[str]:
        """Return the preamble (skipped) lines decoded from ``head`` bytes."""
        sample = self._decode_head(head, self.encoding)
        sep = self.separator if self.separator is not None else self._detect_separator(sample)
        skip = self.skip_rows if self.skip_rows is not None else self._detect_skiprows(sample, sep=sep)
        return sample.splitlines()[:skip]

    @staticmethod
    def _build_rename_map(raw: bytes, encoding: str, skip: int, sep: str) -> dict[str, str]:
        """Map mangled (utf8-lossy) column names to properly-decoded names.

        Decodes the header line at ``raw[skip]`` twice: once with ``encoding``
        (proper names) and once with ``utf-8/errors=replace`` (mangled names, matching
        what polars utf8-lossy produces). Returns ``{mangled: proper}`` for columns
        where the two differ.  Returns an empty dict when all names are identical
        (e.g. ASCII-only headers or ``skip`` beyond the buffered content).
        """
        try:
            proper_cols = DelimTxtParser._decode_head(raw, encoding).splitlines()[skip].split(sep)
            mangled_cols = DelimTxtParser._decode_head(raw, "utf-8").splitlines()[skip].split(sep)
        except IndexError:
            return {}
        return {m: p for m, p in zip(mangled_cols, proper_cols) if m != p}

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Parse ``path`` (local or URL) to a LazyFrame, honouring (and auto-sniffing) config."""
        raw = read_head(path)
        sample = self._decode_head(raw, self.encoding)
        sep = self.separator if self.separator is not None else self._detect_separator(sample)
        skip = self.skip_rows if self.skip_rows is not None else self._detect_skiprows(sample, sep=sep)
        is_utf8 = self.encoding.lower() in ("utf-8", "utf8")
        encoding_arg = "utf8" if is_utf8 else "utf8-lossy"
        source: str | Path = str(path) if is_url(str(path)) else Path(path)
        lf = pl.scan_csv(
            source,
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
        """Return column headers by reading the head bytes of ``path`` (local or URL)."""
        raw = read_head(path)
        sample = self._decode_head(raw, self.encoding)
        sep = self.separator if self.separator is not None else self._detect_separator(sample)
        skip = self.skip_rows if self.skip_rows is not None else self._detect_skiprows(sample, sep=sep)
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
    is_text: ClassVar[bool] = False

    @model_validator(mode="after")
    def _require_header(self) -> "ExcelParser":
        if not self.has_header:
            raise ValueError(
                "Reading data with bdf requires a header row to map columns to the bdf standard. "
                "If your data has no headers, read directly with polars.read_excel(..., has_header=False) "
                "then normalize with the TableNormalizer.normalize() method."
            )
        return self

    def _read_sheet(self, path: str | Path, *, read_options: dict[str, Any], **extra: Any) -> pl.DataFrame:
        """Run ``pl.read_excel`` with the reader's sheet/column config and assert a single sheet."""
        kwargs: dict[str, Any] = {"engine": self.engine, "has_header": self.has_header, **extra}
        if self.sheet_id is not None:
            kwargs["sheet_id"] = self.sheet_id
        if self.sheet_name is not None:
            kwargs["sheet_name"] = self.sheet_name
        if self.columns is not None:
            kwargs["columns"] = self.columns
        if read_options:
            kwargs["read_options"] = read_options
        df = pl.read_excel(path, **kwargs)
        if isinstance(df, dict):
            raise ValueError("ExcelParser expects a single sheet; specify `sheet_id` or `sheet_name` to disambiguate.")
        return df

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Parse the configured sheet of ``path`` (local or URL) to a LazyFrame."""
        source: str | Path = str(path) if is_url(str(path)) else Path(path)
        df = self._read_sheet(
            source,
            read_options=dict(self.read_options or {}),
            drop_empty_rows=self.drop_empty_rows,
        )
        return df.with_columns(pl.all().cast(pl.Utf8, strict=False)).lazy()

    def read_column_headings(self, path: str | Path) -> list[str]:
        """Return the header row without reading data rows (n_rows=0)."""
        source: str | Path = str(path) if is_url(str(path)) else Path(path)
        return self._read_sheet(source, read_options={**(self.read_options or {}), "n_rows": 0}).columns


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
    is_text: ClassVar[bool] = False

    def _load(self, path: Path, var_names: list[str]) -> dict[str, Any]:
        try:
            from scipy.io import loadmat
        except ImportError as exc:
            raise RuntimeError("MatParser requires scipy. Install with `pip install scipy`.") from exc
        return loadmat(str(path), variable_names=var_names, squeeze_me=True)

    def _read_raw(self, path: str | Path) -> pl.LazyFrame:
        """Load the variables named by :attr:`normalizer` from the .mat file into a LazyFrame.

        Variable names come from ``self.normalizer.known_header_names()`` (a .mat
        file has no header row). When ``path`` is an ``http(s)://`` URL, the file
        is downloaded to a temporary path first and cleaned up after loading.
        """
        import numpy as np

        var_names = self.normalizer.known_header_names()
        if is_url(str(path)):
            try:
                import requests
            except ImportError as exc:
                raise ImportError("URL support requires 'requests'. Install with: pip install requests") from exc
            resp = requests.get(str(path), timeout=120)
            if not resp.ok:
                raise ValueError(f"HTTP {resp.status_code} downloading {path}")
            with tempfile.NamedTemporaryFile(suffix=".mat", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = Path(tmp.name)
            try:
                mat = self._load(tmp_path, var_names)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
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
        """
        var_names = self.normalizer.known_header_names()
        mat = self._load(Path(path), var_names)
        return [name for name in var_names if name in mat]
