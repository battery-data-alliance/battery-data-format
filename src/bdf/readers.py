"""Mechanics-only file readers: ``DelimTxtReader``, ``ExcelReader``, ``MatReader``.

Each reader wraps polars (DelimTxtReader, ExcelReader) or scipy (MatReader) file parsers
and turns bytes → :class:`polars.LazyFrame` for one file-format family, keyed by a
``name`` discriminator (``"txt"`` / ``"excel"`` / ``"mat"``). Readers carry parse
configuration and behaviour **only** — no vendor identity, magic, metadata, or normalizer.
Source resolution and the normalize step live in :mod:`bdf.datasources` and
:mod:`bdf.normalizers` respectively.

A fixed-size ``head`` byte buffer (read once by ``detect()``) may be threaded
into ``read()`` / ``headers()``. The CSV reader reuses it for preamble decoding
and separator/skip sniffing; binary readers (Excel/MAT) accept it for symmetry
but re-open the file to parse.

Polars is licensed under MIT: https://github.com/pola-rs/polars/blob/main/LICENSE
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

HEAD_BYTES = 65536  # large enough for long text preambles


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


class DelimTxtReader(BaseModel):
    """Wraps :func:`polars.scan_csv` for delimited text files (.csv/.tsv/.txt/.dat).

    Adds auto-detection and encoding handling on top of polars' CSV parser.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["txt"] = "txt"
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
    def read_head(path: Path, n_bytes: int = HEAD_BYTES) -> bytes:
        """Read up to ``n_bytes`` raw bytes from the start of ``path``.

        Strips a leading UTF-8 BOM so header reconstruction, separator/skip
        sniffing, and magic matching agree with polars, which always drops the
        BOM from the parsed frame (under both ``utf8`` and ``utf8-lossy``).
        """
        with open(path, "rb") as fh:
            return fh.read(n_bytes).removeprefix(b"\xef\xbb\xbf")

    @staticmethod
    def _decode_head(head: bytes, encoding: str = "utf-8") -> str:
        """Decode head bytes to text, dropping any trailing partial line."""
        text = head.decode(encoding, errors="replace")
        last_nl = text.rfind("\n")
        if last_nl >= 0:
            text = text[:last_nl]
        return text

    @model_validator(mode="after")
    def _require_header(self) -> "DelimTxtReader":
        if not self.has_header:
            raise ValueError(
                "Reading data with bdf requires a header row to map columns to the bdf standard. "
                "If your data has no headers, read directly with polars.scan_csv(..., has_header=False) "
                "then normalize with the Normalizer.normalize() method."
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
    def _detect_separator(sample: str, candidates: tuple[str, ...] = (",", "\t", ";", "|")) -> str:
        """Detect the field separator: the candidate giving the longest consistent
        run of equal field counts, breaking ties by data-row numeric ratio."""
        lines = sample.splitlines()
        best_sep = ","
        best_score = 0
        best_ratio = -1.0
        for sep in candidates:
            start, run_len, fc = DelimTxtReader._best_run(lines, sep)
            score = run_len * fc
            if score == 0:
                continue
            data_idx = start + 1
            ratio = DelimTxtReader._numeric_ratio(lines[data_idx], sep) if data_idx < len(lines) else 0.0
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
            sep = DelimTxtReader._detect_separator(sample)
        start, run_len, _ = DelimTxtReader._best_run(lines, sep)
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
            proper_cols = DelimTxtReader._decode_head(raw, encoding).splitlines()[skip].split(sep)
            mangled_cols = DelimTxtReader._decode_head(raw, "utf-8").splitlines()[skip].split(sep)
        except IndexError:
            return {}
        return {m: p for m, p in zip(mangled_cols, proper_cols) if m != p}

    def read(self, path: str | Path, head: bytes | None = None, *, var_names: list[str] | None = None) -> pl.LazyFrame:
        """Parse ``path`` to a LazyFrame, honouring (and auto-sniffing) config.

        ``head`` is reused for separator/skip sniffing when provided; the full
        data is still pulled lazily via :func:`polars.scan_csv`. ``var_names``
        is accepted for interface uniformity with ``MatReader`` and ignored.
        """
        path = Path(path)
        raw = head if head is not None else self.read_head(path)
        sample = self._decode_head(raw, self.encoding)
        sep = self.separator if self.separator is not None else self._detect_separator(sample)
        skip = self.skip_rows if self.skip_rows is not None else self._detect_skiprows(sample, sep=sep)
        is_utf8 = self.encoding.lower() in ("utf-8", "utf8")
        encoding_arg = "utf8" if is_utf8 else "utf8-lossy"
        lf = pl.scan_csv(
            path,
            skip_rows=skip,
            separator=sep,
            has_header=self.has_header,
            infer_schema=False,
            encoding=encoding_arg,
        )
        if not is_utf8:
            rename_map = self._build_rename_map(raw, self.encoding, skip, sep)
            if rename_map:
                lf = lf.rename(rename_map)
        decimal_comma = self.decimal_comma if self.decimal_comma is not None else self._sniff_decimal(lf)
        return self._coerce_decimal(lf, decimal_comma)

    def headers(self, path: str | Path, head: bytes | None = None, *, var_names: list[str] | None = None) -> list[str]:
        """Return column headers derived from the in-memory head bytes."""
        raw = head if head is not None else self.read_head(Path(path))
        sample = self._decode_head(raw, self.encoding)
        sep = self.separator if self.separator is not None else self._detect_separator(sample)
        skip = self.skip_rows if self.skip_rows is not None else self._detect_skiprows(sample, sep=sep)
        lines = sample.splitlines()
        if skip >= len(lines):
            return []
        return lines[skip].split(sep)


class ExcelReader(BaseModel):
    """Wraps :func:`polars.read_excel` for .xlsx/.xlsm/.xls files.

    Delegates to polars' Excel parser with configurable engines (calamine, openpyxl, xlsx2csv).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["excel"] = "excel"
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
    def _require_header(self) -> "ExcelReader":
        if not self.has_header:
            raise ValueError(
                "Reading data with bdf requires a header row to map columns to the bdf standard. "
                "If your data has no headers, read directly with polars.read_excel(..., has_header=False) "
                "then normalize with the Normalizer.normalize() method."
            )
        return self

    def _read_sheet(self, path: Path, *, read_options: dict[str, Any], **extra: Any) -> pl.DataFrame:
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
            raise ValueError("ExcelReader expects a single sheet; specify `sheet_id` or `sheet_name` to disambiguate.")
        return df

    def read(self, path: str | Path, head: bytes | None = None, *, var_names: list[str] | None = None) -> pl.LazyFrame:
        """Parse the configured sheet of ``path`` to a LazyFrame.

        ``head`` and ``var_names`` are accepted for interface symmetry but
        unused: Excel is binary and must re-open the file to parse.
        """
        df = self._read_sheet(
            Path(path),
            read_options=dict(self.read_options or {}),
            drop_empty_rows=self.drop_empty_rows,
        )
        return df.with_columns(pl.all().cast(pl.Utf8, strict=False)).lazy()

    def headers(self, path: str | Path, head: bytes | None = None, *, var_names: list[str] | None = None) -> list[str]:
        """Return the header row without reading data rows (n_rows=0).

        Merges ``{"n_rows": 0}`` into the effective ``read_options``, overriding
        any caller-supplied ``n_rows``. ``var_names`` is accepted for interface
        uniformity and ignored.
        """
        return self._read_sheet(Path(path), read_options={**(self.read_options or {}), "n_rows": 0}).columns


class MatReader(BaseModel):
    """Wraps :func:`scipy.io.loadmat` for .mat (MATLAB) files.

    Converts loaded variables into polars LazyFrames. Variable names to load are
    supplied per call (by the resolved ``DataSource``'s normalizer), keeping the
    reader free of vendor data.
    """

    model_config = ConfigDict(frozen=True)

    name: Literal["mat"] = "mat"

    base_exts: ClassVar[frozenset[str]] = frozenset({".mat"})
    is_text: ClassVar[bool] = False

    def _load(self, path: Path, var_names: list[str]) -> dict[str, Any]:
        try:
            from scipy.io import loadmat
        except ImportError as exc:
            raise RuntimeError("MatReader requires scipy. Install with `pip install scipy`.") from exc
        return loadmat(str(path), variable_names=var_names, squeeze_me=True)

    def read(self, path: str | Path, head: bytes | None = None, *, var_names: list[str]) -> pl.LazyFrame:
        """Load ``var_names`` from the .mat file into a LazyFrame.

        ``head`` is accepted for interface symmetry but unused (binary format).
        """
        path = Path(path)
        import numpy as np

        mat = self._load(path, var_names)
        data: dict[str, Any] = {}
        for name in var_names:
            if name not in mat:
                raise ValueError(f"MatReader: variable {name!r} not found in {path}")
            arr = np.atleast_1d(np.asarray(mat[name]).squeeze())
            if arr.ndim != 1:
                raise ValueError(f"MatReader: variable {name!r} has shape {arr.shape} after squeeze; must be 1-D")
            data[name] = arr.astype(np.float64)
        return pl.DataFrame(data).lazy()

    def headers(self, path: str | Path, head: bytes | None = None, *, var_names: list[str]) -> list[str]:
        """Return the subset of ``var_names`` actually present in the .mat file."""
        path = Path(path)
        mat = self._load(path, var_names)
        return [name for name in var_names if name in mat]


__all__ = ["DelimTxtReader", "ExcelReader", "MatReader", "HEAD_BYTES"]
