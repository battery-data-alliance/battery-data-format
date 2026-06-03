"""Serializable vendor plugins, the ``PLUGINS`` registry, and detection.

A :class:`Plugin` bundles vendor identity (``id`` / ``exts`` / ``magic``),
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
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .normalizers import NORMALIZERS, MetadataParser, Normalizer
from .readers import DelimTxtReader, ExcelReader, MatReader

ReaderUnion = Annotated[DelimTxtReader | ExcelReader | MatReader, Field(discriminator="name")]


class Plugin(BaseModel):
    """A serializable vendor entry: identity + metadata + normalizer + reader."""

    model_config = ConfigDict(frozen=True)

    id: str
    exts: tuple[str, ...] = ()
    magic: tuple[str | bytes, ...] = ()
    metadata: MetadataParser = Field(default_factory=MetadataParser)
    normalizer: Normalizer
    reader: ReaderUnion

    @model_validator(mode="after")
    def _warn_text_only_fields_on_binary(self) -> Plugin:
        """``magic`` / ``metadata`` are only meaningful for text readers."""
        if not self.reader.is_text:
            meta_fields = type(self.metadata).model_fields
            has_configured_pattern = any(getattr(self.metadata, f) != field.default for f, field in meta_fields.items())
            if self.magic or has_configured_pattern:
                warnings.warn(
                    f"Plugin {self.id!r}: `magic`/`metadata` are ignored for "
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
# Built-in plugins
# ---------------------------------------------------------------------------

ARBIN_CSV = Plugin(
    id="arbin_csv",
    normalizer=NORMALIZERS["arbin"],
    reader=DelimTxtReader(),
)

BASYTEC_TXT = Plugin(
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

BIOLOGIC_MPT = Plugin(
    id="biologic_mpt",
    exts=(".mpt",),
    magic=("bt-lab ascii file", "ec-lab ascii file"),
    metadata=MetadataParser(start_time=r"Acquisition started on\s*:\s*(.+)"),
    normalizer=NORMALIZERS["biologic"],
    reader=DelimTxtReader(),
)

DIGATRON_CSV = Plugin(
    id="digatron_csv",
    normalizer=NORMALIZERS["digatron"],
    reader=DelimTxtReader(),
)

LANDT_CSV = Plugin(
    id="landt_csv",
    normalizer=NORMALIZERS["landt_csv"],
    reader=DelimTxtReader(),
)

LANDT_TXT = Plugin(
    id="landt_txt",
    normalizer=NORMALIZERS["landt_txt"],
    reader=DelimTxtReader(),
)

MACCOR_CSV = Plugin(
    id="maccor_csv",
    magic=("today's date", "date of test:"),
    metadata=MetadataParser(start_time=r"Date of Test:,(.+)"),
    normalizer=NORMALIZERS["maccor"],
    reader=DelimTxtReader(),
)

NEWARE_CSV = Plugin(
    id="neware_csv",
    normalizer=NORMALIZERS["neware"],
    reader=DelimTxtReader(),
)

NEWARE_XLSX = Plugin(
    id="neware_xlsx",
    normalizer=NORMALIZERS["neware"],
    reader=ExcelReader(sheet_name="record"),
)

NOVONIX_CSV = Plugin(
    id="novonix_csv",
    magic=("[summary]", "[data]", "novonix uhpc data file", "novonix"),
    normalizer=NORMALIZERS["novonix"],
    reader=DelimTxtReader(),
)


_BUILTIN_PLUGINS: tuple[Plugin, ...] = (
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

PLUGINS: dict[str, Plugin] = {d.id: d for d in _BUILTIN_PLUGINS}


def build_ext_to_reader(plugins: dict[str, Plugin]) -> dict[str, str]:
    """Map every extension (reader base_exts ∪ Plugin.exts) to its reader name.

    Raises ``ValueError`` if any extension is claimed by two different readers.
    """
    ext_to_reader: dict[str, str] = {}
    for ds in plugins.values():
        reader_name = ds.reader.name
        exts = set(type(ds.reader).base_exts) | {e.lower() for e in ds.exts}
        for ext in exts:
            prev = ext_to_reader.setdefault(ext, reader_name)
            if prev != reader_name:
                raise ValueError(f"extension {ext!r} claimed by readers {prev!r} and {reader_name!r}")
    return ext_to_reader


EXT_TO_READER: dict[str, str] = build_ext_to_reader(PLUGINS)


def list_sources() -> list[str]:
    """Return the list of registered plugin IDs."""
    return list(PLUGINS)
