"""Serializable vendor data sources, the ``DATASOURCES`` registry, and detection.

A :class:`DataSource` bundles vendor identity (``id`` / ``exts`` / ``magic``),
metadata extraction, a referenced :class:`~bdf.normalizers.Normalizer`, and a
mechanics-only ``reader`` (discriminated on ``reader.name``). One normalizer
can back several formats — e.g. ``NEWARE_CSV`` and ``NEWARE_XLSX`` both reference
``NORMALIZERS["neware"]``.

Detection is a single pass over one head-byte read:

    ext → reader name (via ``EXT_TO_READER``) → reader filter → distinctive-ext
    narrowing → magic on head bytes → (tie only) per-candidate header sniff.

Dependency direction: this module imports the mechanics-only readers from
:mod:`bdf.readers` and the normalizers from :mod:`bdf.normalizers`; neither imports
back, so there is no cycle.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Annotated

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .normalizers import NORMALIZERS, DateTimeSyn, MetadataParser, Normalizer, ResolvedColumn, normalize
from .readers import HEAD_BYTES, DelimTxtReader, ExcelReader, MatReader

ReaderUnion = Annotated[DelimTxtReader | ExcelReader | MatReader, Field(discriminator="name")]


class DataSource(BaseModel):
    """A serializable vendor entry: identity + metadata + normalizer + reader."""

    model_config = ConfigDict(frozen=True)

    id: str
    exts: tuple[str, ...] = ()
    magic: tuple[str | bytes, ...] = ()
    metadata: MetadataParser = Field(default_factory=MetadataParser)
    normalizer: Normalizer
    reader: ReaderUnion

    @model_validator(mode="after")
    def _warn_text_only_fields_on_binary(self) -> DataSource:
        """``magic`` / ``metadata`` are only meaningful for text readers."""
        if not self.reader.is_text:
            meta_fields = type(self.metadata).model_fields
            has_configured_pattern = any(getattr(self.metadata, f) != field.default for f, field in meta_fields.items())
            if self.magic or has_configured_pattern:
                warnings.warn(
                    f"DataSource {self.id!r}: `magic`/`metadata` are ignored for "
                    f"binary reader {self.reader.name!r} (text-only relevance)",
                    UserWarning,
                    stacklevel=2,
                )
        return self

    def match_ext(self, ext: str) -> bool:
        """True when ``ext`` is one of this source's distinctive extensions."""
        return ext.lower() in self.exts

    def match_magic(self, head: bytes) -> bool:
        """True when any magic token is found in the raw head bytes.

        ``str`` tokens match case-insensitively against the decoded text; ``bytes``
        tokens match as raw byte substrings (all formats).
        """
        if not self.magic:
            return False
        text = head.decode("utf-8", errors="replace").lower()
        for m in self.magic:
            if isinstance(m, bytes):
                if m in head:
                    return True
            elif m.lower() in text:
                return True
        return False

    def score(self, headers: list[str]) -> int:
        """Number of headers the referenced normalizer resolves."""
        return self.normalizer.score(headers)


# ---------------------------------------------------------------------------
# Built-in data sources
# ---------------------------------------------------------------------------

ARBIN_CSV = DataSource(
    id="arbin_csv",
    normalizer=NORMALIZERS["arbin"],
    reader=DelimTxtReader(),
)

BASYTEC_TXT = DataSource(
    id="basytec_txt",
    exts=(".dat",),
    magic=(
        "resultfile from basytec battery test system",
        "basytec battery test system",
    ),
    metadata=MetadataParser(start_time=r"~Start of Test:\s*(.+)"),
    normalizer=NORMALIZERS["basytec"],
    reader=DelimTxtReader(encoding="latin-1"),
)

BIOLOGIC_MPT = DataSource(
    id="biologic_mpt",
    exts=(".mpt",),
    magic=("bt-lab ascii file", "ec-lab ascii file"),
    metadata=MetadataParser(start_time=r"Acquisition started on\s*:\s*(.+)"),
    normalizer=NORMALIZERS["biologic"],
    reader=DelimTxtReader(),
)

DIGATRON_CSV = DataSource(
    id="digatron_csv",
    normalizer=NORMALIZERS["digatron"],
    reader=DelimTxtReader(),
)

LANDT_CSV = DataSource(
    id="landt_csv",
    normalizer=NORMALIZERS["landt_csv"],
    reader=DelimTxtReader(),
)

LANDT_TXT = DataSource(
    id="landt_txt",
    normalizer=NORMALIZERS["landt_txt"],
    reader=DelimTxtReader(),
)

MACCOR_CSV = DataSource(
    id="maccor_csv",
    magic=("today's date", "date of test:"),
    metadata=MetadataParser(start_time=r"Date of Test:,(.+)"),
    normalizer=NORMALIZERS["maccor"],
    reader=DelimTxtReader(),
)

NEWARE_CSV = DataSource(
    id="neware_csv",
    normalizer=NORMALIZERS["neware"],
    reader=DelimTxtReader(),
)

NEWARE_XLSX = DataSource(
    id="neware_xlsx",
    normalizer=NORMALIZERS["neware"],
    reader=ExcelReader(sheet_name="record"),
)

NOVONIX_CSV = DataSource(
    id="novonix_csv",
    magic=("[summary]", "[data]", "novonix uhpc data file", "novonix"),
    normalizer=NORMALIZERS["novonix"],
    reader=DelimTxtReader(),
)


_BUILTIN_DATASOURCES: tuple[DataSource, ...] = (
    ARBIN_CSV,
    BASYTEC_TXT,
    BIOLOGIC_MPT,
    DIGATRON_CSV,
    LANDT_CSV,
    LANDT_TXT,
    MACCOR_CSV,
    NEWARE_CSV,
    NEWARE_XLSX,
    NOVONIX_CSV,
)

DATASOURCES: dict[str, DataSource] = {d.id: d for d in _BUILTIN_DATASOURCES}


def build_ext_to_reader(datasources: dict[str, DataSource]) -> dict[str, str]:
    """Map every extension (reader base_exts ∪ DataSource.exts) to its reader name.

    Raises ``ValueError`` if any extension is claimed by two different readers.
    """
    ext_to_reader: dict[str, str] = {}
    for ds in datasources.values():
        reader_name = ds.reader.name
        exts = set(type(ds.reader).base_exts) | {e.lower() for e in ds.exts}
        for ext in exts:
            prev = ext_to_reader.setdefault(ext, reader_name)
            if prev != reader_name:
                raise ValueError(f"extension {ext!r} claimed by readers {prev!r} and {reader_name!r}")
    return ext_to_reader


EXT_TO_READER: dict[str, str] = build_ext_to_reader(DATASOURCES)


# ---------------------------------------------------------------------------
# Detection + pipeline
# ---------------------------------------------------------------------------


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


def list_sources() -> list[str]:
    """Return the list of registered data source IDs."""
    return list(DATASOURCES)


__all__ = [
    "ARBIN_CSV",
    "BASYTEC_TXT",
    "BIOLOGIC_MPT",
    "DIGATRON_CSV",
    "LANDT_CSV",
    "LANDT_TXT",
    "MACCOR_CSV",
    "NEWARE_CSV",
    "NEWARE_XLSX",
    "NOVONIX_CSV",
    "DATASOURCES",
    "DataSource",
    "EXT_TO_READER",
    "HEAD_BYTES",
    "build_ext_to_reader",
    "detect",
    "read",
    "list_sources",
]
