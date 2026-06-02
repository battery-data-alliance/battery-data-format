# src/bdf/io.py
from __future__ import annotations

import csv
import json
import warnings
from pathlib import Path

import pandas as pd
import polars as pl

from bdf.datasources import DATASOURCES, EXT_TO_READER, DataSource
from bdf.normalizers import DateTimeSyn, Normalizer, ResolvedColumn, normalize
from bdf.readers import DelimTxtReader


def _normalizer_var_names(normalizer: Normalizer) -> list[str]:
    """Source-header names referenced by a normalizer's mapped fields (for MAT)."""
    names: list[str] = []
    for _, spec in normalizer:
        if isinstance(spec, ResolvedColumn):
            names.append(spec.source_header)
        elif isinstance(spec, list):
            for s in spec:
                names.append(s.syn.exemplar if isinstance(s, DateTimeSyn) else s.exemplar)
    return names


def _score_by_headers(cands: list[DataSource], path: Path, head: bytes | None) -> DataSource:
    """Return the highest-scoring candidate by header match.

    Per-candidate sniff failures score as -1 and lose to any positive score.
    Raises :exc:`ValueError` if no candidate scores above zero.
    """

    def safe_score(ds: DataSource) -> int:
        try:
            return ds.score(ds.reader.headers(path, head, var_names=_normalizer_var_names(ds.normalizer)))
        except Exception:
            return -1

    scored = [(d, safe_score(d)) for d in cands]
    best_score = max(s for _, s in scored)
    if best_score <= 0:
        raise ValueError(f"no data source matched {path}")
    winners = [d for d, s in scored if s == best_score]
    if len(winners) > 1:
        ids = ", ".join(d.id for d in winners)
        raise ValueError(f"ambiguous match for {path}: {ids} all scored {best_score}")
    return winners[0]


def detect(path: str | Path, head: bytes | None = None) -> DataSource:
    """Resolve the :class:`DataSource` for ``path`` in a single pass.

    Stages: ext → reader name; filter candidates to that reader; narrow to
    distinctive-ext matches if any; magic match on head bytes; on a remaining
    tie, score each candidate against headers sniffed with its own reader config.
    """
    path = Path(path)
    if head is None:
        head = DelimTxtReader.read_head(path)

    # ext → reader name
    ext = path.suffix.lower()
    if ext not in EXT_TO_READER:
        raise ValueError(f"no reader registered for extension {ext!r}")
    reader_name = EXT_TO_READER[ext]

    # candidates using that reader
    cands = [d for d in DATASOURCES.values() if d.reader.name == reader_name]

    # narrow to distinctive-ext matches if any
    distinctive = [d for d in cands if d.match_ext(ext)]
    cands = distinctive or cands

    # narrow to magic matches if any
    hits = [d for d in cands if d.match_magic(head)]
    cands = hits or cands

    if not cands:
        raise ValueError(f"no data source matched {path}")
    if len(cands) == 1:
        return cands[0]

    return _score_by_headers(cands, path, head)


def read(
    path: str | Path,
    *,
    datasource: DataSource | None = None,
    normalizer: Normalizer | dict[str, str] | None = None,
    include_optional: bool = True,
    extra_columns: dict[str, str] | None = None,
    lazy: bool = False,
) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
    """Read ``path`` to BDF-canonical form, returning ``(df, metadata)``.

    Resolution: an explicit ``datasource`` bypasses :func:`detect`; otherwise the
    source is detected from one head read. An explicit ``normalizer`` overrides the
    resolved source's normalizer for the normalize step.
    """
    path = Path(path)
    head = DelimTxtReader.read_head(path)
    ds = datasource if datasource is not None else detect(path, head)

    eff_normalizer: Normalizer
    if isinstance(normalizer, dict):
        eff_normalizer = Normalizer.from_column_map(normalizer)
    elif isinstance(normalizer, Normalizer):
        eff_normalizer = normalizer
    else:
        eff_normalizer = ds.normalizer

    reader = ds.reader
    lf = reader.read(path, head, var_names=_normalizer_var_names(eff_normalizer))

    bdf_lf = normalize(
        lf,
        include_optional=include_optional,
        normalizer=eff_normalizer,
        extra_columns=extra_columns,
    )

    metadata: dict = {"source": ds.id}
    if reader.is_text:
        preamble = reader.preamble(head)
        if preamble:
            for key, val in ds.metadata.parse(preamble).items():
                metadata[key] = val

    if lazy:
        return bdf_lf, metadata
    return bdf_lf.collect() if isinstance(bdf_lf, pl.LazyFrame) else bdf_lf, metadata


_FMT_EXTS = {
    "csv": {".csv", ".bdf.csv"},
    "parquet": {".parquet", ".bdf.parquet"},
    "feather": {".feather", ".bdf.feather"},
    "json": {".json", ".bdf.json"},
}
_COMPRESS = {".gz": "gzip", ".bz2": "bz2", ".xz": "xz", ".zst": "zstd"}


def _detect_format(path: Path) -> str:
    sfx = "".join(path.suffixes).lower()
    for fmt, exts in _FMT_EXTS.items():
        if any(sfx.endswith(e) for e in exts):
            return fmt
    last = path.suffix.lower()
    if last in (".csv", ".parquet", ".feather", ".json"):
        return last.lstrip(".")
    raise ValueError(f"Unknown BDF artifact format: {path.name}")


def _detect_compression(path: Path) -> str | None:
    s = str(path).lower()
    for ext, comp in _COMPRESS.items():
        if s.endswith(ext):
            return comp
    return None


def _meta_sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".metadata.json")


def _coalesce_into(target: pd.Series, incoming: pd.Series) -> pd.Series:
    return target.where(target.notna(), incoming)


def _label_maps() -> tuple[dict[str, str], dict[str, str]]:
    """
    Build two maps:
      - pref_label -> machine label (notation), using non-deprecated canonical targets.
      - machine label (notation) -> human pref_label, using non-deprecated canonical targets.
    """
    from . import spec

    base_preferred: dict[str, str] = {}
    for q, s in spec.COLUMN_ONTOLOGY:
        if s.deprecated:
            continue
        base = s.label.split(" / ", 1)[0].strip().lower()
        base_preferred.setdefault(base, q)

    pref_to_machine: dict[str, str] = {}
    machine_to_pref: dict[str, str] = {}

    for q, s in spec.COLUMN_ONTOLOGY:
        source_pref = s.label
        source_notation = s.effective_notation

        target_q = q
        if s.deprecated:
            base = source_pref.split(" / ", 1)[0].strip().lower()
            target_q = base_preferred.get(base, q)

        target = getattr(spec.COLUMN_ONTOLOGY, target_q)
        target_pref = target.label
        target_notation = target.effective_notation

        pref_to_machine.setdefault(source_pref, target_notation)
        machine_to_pref.setdefault(source_notation, target_pref)

    return pref_to_machine, machine_to_pref


def _serialize_labels(df: pd.DataFrame, *, human: bool) -> pd.DataFrame:
    out = df.copy()
    pref_to_machine, machine_to_pref = _label_maps()

    if human:
        for source, target in machine_to_pref.items():
            if source not in out.columns:
                continue
            if source == target:
                continue
            if target in out.columns:
                out[target] = _coalesce_into(out[target], out[source])
                out.drop(columns=[source], inplace=True)
            else:
                out.rename(columns={source: target}, inplace=True)
        return out

    for source, target in pref_to_machine.items():
        if source not in out.columns:
            continue
        if source == target:
            continue
        if target in out.columns:
            out[target] = _coalesce_into(out[target], out[source])
            out.drop(columns=[source], inplace=True)
        else:
            out.rename(columns={source: target}, inplace=True)
    return out


def load(pathlike) -> pd.DataFrame:
    p = Path(pathlike)
    if not p.exists():
        raise FileNotFoundError(p.name)
    fmt = _detect_format(p)
    comp = _detect_compression(p)

    try:
        df = None
        if fmt == "csv":
            # strict CSV: no banner rows, uniform columns
            df = pd.read_csv(
                p,
                engine="python",  # better error messages for malformed rows
                sep=",",
                quoting=csv.QUOTE_MINIMAL,
                on_bad_lines="error",
                skip_blank_lines=True,
                compression=comp,
            )
        elif fmt == "parquet":
            df = pd.read_parquet(p)
        elif fmt == "feather":
            df = pd.read_feather(p)
        elif fmt == "json":
            df = pd.read_json(p, lines=True, compression=comp)
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        # Always expose human canonical labels in-memory.
        from .normalizers import canonicalize_legacy_labels

        df, legacy = canonicalize_legacy_labels(df)
        if legacy:
            warnings.warn(
                "Legacy BDF column labels detected (skos:altLabel/notation). They were normalized to preferred labels.",
                stacklevel=2,
            )
        return _serialize_labels(df, human=True)
    except Exception as e:
        # Re-raise with a short, path-sanitized message
        emsg = str(e)
        raise ValueError(f"Failed to parse BDF {fmt.upper()} file: {p.name}: {emsg}") from e


def save(
    df: pd.DataFrame,
    pathlike,
    *,
    metadata: dict | None = None,
    index: bool = False,
    human: bool = False,
    **opts,
) -> None:
    p = Path(pathlike)
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = _detect_format(p)
    comp = _detect_compression(p)

    try:
        from .normalizers import canonicalize_legacy_labels

        df, _legacy = canonicalize_legacy_labels(df)
    except Exception:
        pass
    df = _serialize_labels(df, human=human)

    if fmt == "csv":
        df.to_csv(p, index=index, compression=comp, **opts)
    elif fmt == "parquet":
        df.to_parquet(p, index=index, **opts)
    elif fmt == "feather":
        df.to_feather(p, **opts)
    elif fmt == "json":
        df.to_json(p, orient="records", lines=True, compression=comp, **opts)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    if metadata:
        _meta_sidecar(p).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
