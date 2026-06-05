"""Serializable vendor plugins, the ``PLUGINS`` registry, and detection.

A :class:`Plugin` is a pure data pair ``(table, metadata)`` — no vendor identity
fields. The ``table`` is a :class:`~bdf.table_parsers.TableParser` carrying its own
:class:`~bdf.normalizers.TableNormalizer`; the ``metadata`` is a
:class:`~bdf.metadata_parsers.MetadataParser`. The plugin ``id`` is the key in the
:data:`PLUGINS` dict.

Detection is a three-stage composable pipeline:

    ``detect_from_ext(path)`` → ``detect_from_metadata(path)`` → ``detect_from_columns(path)``

Each stage operates on a ``dict[str, Plugin]`` and accepts an optional ``cands``
argument that defaults to :data:`PLUGINS`. :func:`detect` orchestrates the three
stages, returning early when candidates narrow to exactly one.

Dependency direction: this module imports the table parsers from
:mod:`bdf.table_parsers`, the normalizers from :mod:`bdf.normalizers`, and the
metadata parsers from :mod:`bdf.metadata_parsers`; none import back, so there is no
cycle.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .head_utils import is_url
from .metadata_parsers import JsonSidecarParser, MetadataParser, MetadataSchema, TxtPreambleParser
from .normalizers import NORMALIZERS
from .table_parsers import DelimTxtParser, ExcelParser, MatParser, _ext_from_url

TableParserUnion = Annotated[DelimTxtParser | ExcelParser | MatParser, Field(discriminator="kind")]
MetadataUnion = Annotated[
    MetadataParser | TxtPreambleParser | JsonSidecarParser,
    Field(discriminator="kind"),
]


class Plugin(BaseModel):
    """A serializable vendor entry: ``(table_parser, metadata_parser)`` pair.

    ``table_parser`` is a :class:`TableParser` carrying its own :class:`TableNormalizer`;
    ``metadata_parser`` defaults to an inert :class:`MetadataParser`. The plugin identity
    is the key in :data:`PLUGINS`, not a field on the model. Extra fields (e.g. the
    old ``reader``/``normalizer``/``id``) raise a ``ValidationError`` as a migration
    guard.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table_parser: TableParserUnion
    metadata_parser: MetadataUnion = Field(default_factory=MetadataParser)


# ---------------------------------------------------------------------------
# Built-in plugins  (id = PLUGINS registry key)
#
# Each plugin's normalizer lives inside its table parser. ``neware_csv`` and
# ``neware_xlsx`` share the one ``NORMALIZERS["neware"]`` instance across two
# distinct table parsers.
# ---------------------------------------------------------------------------

ARBIN_CSV = Plugin(
    table_parser=DelimTxtParser(normalizer=NORMALIZERS["arbin"]),
)

BASYTEC_TXT = Plugin(
    table_parser=DelimTxtParser(
        normalizer=NORMALIZERS["basytec"],
        encoding="latin-1",
        unique_exts=frozenset({".dat"}),
    ),
    metadata_parser=TxtPreambleParser(
        magic=(
            "resultfile from basytec battery test system",
            "basytec battery test system",
        ),
        encoding="latin-1",
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=r"~Start of Test:\s*(.+)"),
    ),
)

BIOLOGIC_MPT = Plugin(
    table_parser=DelimTxtParser(
        normalizer=NORMALIZERS["biologic"],
        unique_exts=frozenset({".mpt"}),
        encoding="latin-1",
    ),
    metadata_parser=TxtPreambleParser(
        magic=("bt-lab ascii file", "ec-lab ascii file"),
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=r"Acquisition started on\s*:\s*(.+)"),
    ),
)

DIGATRON_CSV = Plugin(
    table_parser=DelimTxtParser(normalizer=NORMALIZERS["digatron"]),
)

LANDT_CSV = Plugin(
    table_parser=DelimTxtParser(normalizer=NORMALIZERS["landt_csv"], truncate_ragged_lines=True),
)

LANDT_TXT = Plugin(
    table_parser=DelimTxtParser(normalizer=NORMALIZERS["landt_txt"], truncate_ragged_lines=True),
)

MACCOR_CSV = Plugin(
    table_parser=DelimTxtParser(normalizer=NORMALIZERS["maccor"]),
    metadata_parser=TxtPreambleParser(
        magic=("today's date ,", "date of test:,"),
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=r"Date of Test:,(.+)"),
    ),
)

NEWARE_CSV = Plugin(
    table_parser=DelimTxtParser(normalizer=NORMALIZERS["neware"]),
)

NEWARE_XLSX = Plugin(
    table_parser=ExcelParser(normalizer=NORMALIZERS["neware"], sheet_name="record"),
)

NOVONIX_CSV = Plugin(
    table_parser=DelimTxtParser(normalizer=NORMALIZERS["novonix"]),
    metadata_parser=TxtPreambleParser(magic=("[summary]", "[data]", "novonix uhpc data file", "novonix")),
)


PLUGINS: dict[str, Plugin] = {
    "arbin_csv": ARBIN_CSV,
    "basytec_txt": BASYTEC_TXT,
    "biologic_mpt": BIOLOGIC_MPT,
    "digatron_csv": DIGATRON_CSV,
    "landt_csv": LANDT_CSV,
    "landt_txt": LANDT_TXT,
    "maccor_csv": MACCOR_CSV,
    "neware_csv": NEWARE_CSV,
    "neware_xlsx": NEWARE_XLSX,
    "novonix_csv": NOVONIX_CSV,
}


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------


def detect_from_ext(
    path: str | Path,
    cands: dict[str, Plugin] | None = None,
) -> dict[str, Plugin]:
    """Return plugins from ``cands`` whose table parser handles the extension of ``path``.

    Defaults to :data:`PLUGINS`. Raises :exc:`ValueError` when no candidate handles
    the extension.
    """
    if cands is None:
        cands = PLUGINS
    path_str = str(path)
    ext = _ext_from_url(path_str) if is_url(path_str) else Path(path).suffix.lower()
    matched = {id_: p for id_, p in cands.items() if p.table_parser.matches_ext(ext)}
    if not matched:
        raise ValueError(f"no table parser registered for extension {ext!r}")
    return matched


def detect_from_metadata(
    path: str | Path,
    cands: dict[str, Plugin] | None = None,
) -> dict[str, Plugin]:
    """Return plugins from ``cands`` whose metadata parser matches ``path``.

    Defaults to :data:`PLUGINS`. When no candidate matches (file has no identifying
    preamble), returns ``cands`` unchanged so the pipeline continues to column scoring.
    """
    if cands is None:
        cands = PLUGINS
    path_str = str(path)
    path_arg: str | Path = path_str if is_url(path_str) else Path(path)
    matched = {id_: p for id_, p in cands.items() if p.metadata_parser.matches(path_arg)}
    return matched if matched else cands


def detect_from_columns(
    path: str | Path,
    cands: dict[str, Plugin] | None = None,
) -> tuple[str, Plugin]:
    """Return ``(plugin_id, Plugin)`` for the highest-scoring candidate on ``path``'s column headers.

    Defaults to :data:`PLUGINS`. Raises :exc:`ValueError` if no candidate scores above
    zero, or if the top score is tied between multiple candidates.
    """
    if cands is None:
        cands = PLUGINS
    path_str = str(path)
    path_arg: str | Path = path_str if is_url(path_str) else Path(path)
    scored = {id_: p.table_parser.normalizer_score(path_arg) for id_, p in cands.items()}
    best_score = max(scored.values(), default=0)
    if best_score == 0:
        raise ValueError(f"no candidate scored above zero on column headers for {path!r}")
    winners = {id_: p for id_, p in cands.items() if scored[id_] == best_score}
    if len(winners) > 1:
        raise ValueError(f"ambiguous match for {path!r}: {', '.join(winners)}")
    return next(iter(winners.items()))


def detect(path: str | Path) -> tuple[str, Plugin]:
    """Resolve ``(plugin_id, Plugin)`` for ``path`` (local file or URL).

    Calls :func:`detect_from_ext` → :func:`detect_from_metadata` → :func:`detect_from_columns`
    in sequence, returning early after any stage that narrows candidates to exactly one.
    """
    cands = detect_from_ext(path)
    if len(cands) == 1:
        return next(iter(cands.items()))
    cands = detect_from_metadata(path, cands)
    if len(cands) == 1:
        return next(iter(cands.items()))
    return detect_from_columns(path, cands)


def list_sources() -> list[str]:
    """Return the list of registered plugin IDs."""
    return list(PLUGINS)
