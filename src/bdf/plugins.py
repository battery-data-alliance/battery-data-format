"""Serializable vendor plugins, the ``PLUGINS`` registry, and detection.

A :class:`Plugin` is a pure data pair ``(table, metadata)`` — no vendor identity
fields. The ``table`` is a :class:`~bdf.table_parsers.TableParser` carrying its own
:class:`~bdf.table_normalizers.TableNormalizer`; the ``metadata`` is a
:class:`~bdf.metadata_parsers.MetadataParser`. The plugin ``id`` is the key in the
:data:`PLUGINS` dict.

Detection is a three-stage composable pipeline:

    ``detect_from_ext_or_magic_bytes(path)`` → ``detect_from_metadata(path)`` → ``detect_from_columns(path)``

Each stage operates on a ``dict[str, Plugin]`` and accepts an optional ``cands``
argument that defaults to :data:`PLUGINS`. :func:`detect` orchestrates the three
stages, returning early when candidates narrow to exactly one.

Dependency direction: this module imports the table parsers from
:mod:`bdf.table_parsers`, the normalizers from :mod:`bdf.table_normalizers`, and the
metadata parsers from :mod:`bdf.metadata_parsers`; none import back, so there is no
cycle.
"""

from __future__ import annotations

import json
import re
from difflib import get_close_matches
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .file_utils import is_url, read_head, resolve_source
from .metadata_parsers import JsonSidecarParser, MetadataParser, MetadataSchema, TxtPreambleParser
from .table_normalizers import BDF_NORMALIZER, NDA_NORMALIZER, NORMALIZERS, TableNormalizer
from .table_parsers import DelimTxtParser, ExcelParser, MatParser, NDAParser, ParquetParser

try:
    import yaml

    _HAS_YAML = True
    _YAML_IMPORT_ERROR: ImportError | None = None
except ImportError as _exc:
    _HAS_YAML = False
    _YAML_IMPORT_ERROR = _exc

TableParserUnion = Annotated[
    DelimTxtParser | ExcelParser | MatParser | ParquetParser | NDAParser,
    Field(discriminator="kind"),
]
MetadataUnion = Annotated[
    MetadataParser | TxtPreambleParser | JsonSidecarParser,
    Field(discriminator="kind"),
]


class PluginDict(dict):
    """Dict subclass for plugins that suggests close matches on KeyError.

    Args:
        cutoff: Similarity threshold for suggestions (0.0-1.0). Defaults to 0.6.
    """

    def __init__(self, *args, cutoff=0.6, **kwargs):
        """Initialize the dict with an optional fuzzy-match cutoff.

        Args:
            *args: Positional arguments forwarded to ``dict.__init__``.
            cutoff: Similarity threshold (0.0-1.0) for KeyError suggestions. Defaults to 0.6.
            **kwargs: Keyword arguments forwarded to ``dict.__init__``.
        """
        super().__init__(*args, **kwargs)
        self.cutoff = cutoff

    def __getitem__(self, key):
        """Get plugin by key, suggesting close matches on KeyError.

        Args:
            key: Plugin ID to retrieve.

        Returns:
            The plugin for the given key.

        Raises:
            KeyError: If key not found, with suggestions for similar keys.
        """
        if key in self:
            return super().__getitem__(key)
        matches = get_close_matches(str(key), map(str, self.keys()), n=3, cutoff=self.cutoff)
        if matches:
            suggestions = ", ".join(f"'{m}'" for m in matches)
            raise KeyError(f"No plugin named '{key}', did you mean: {suggestions}?")
        raise KeyError(f"No plugin named '{key}'")


class Plugin(BaseModel):
    """A serializable vendor entry: ``(table_parser, metadata_parser)`` pair.

    ``table_parser`` is a :class:`TableParser` carrying its own :class:`TableNormalizer`;
    ``metadata_parser`` defaults to an inert :class:`MetadataParser`. The plugin identity
    is the key in :data:`PLUGINS`, not a field on the model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table_parser: TableParserUnion
    metadata_parser: MetadataUnion = Field(default_factory=MetadataParser)

    def with_normalizer(self, normalizer: TableNormalizer) -> "Plugin":
        """Return a copy of this plugin with the table parser's normalizer replaced.

        Hides the two-level ``model_copy`` chain (``table_parser`` → ``normalizer``)
        needed to swap a plugin's column mapping. ``metadata_parser`` is unchanged.

        Args:
            normalizer: New normalizer to install into the table_parser.

        Returns:
            New frozen Plugin with table_parser.normalizer replaced.
        """
        new_table_parser = self.table_parser.model_copy(update={"normalizer": normalizer})
        return self.model_copy(update={"table_parser": new_table_parser})


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
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"~Start of Test:\s*(.+)")),
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
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"Acquisition started on\s*:\s*(.+)")),
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
        regex_patterns=MetadataSchema[re.Pattern[str]](start_time=re.compile(r"Date of Test:,(.+)")),
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

NEWARE_NDA = Plugin(table_parser=NDAParser(normalizer=NDA_NORMALIZER))

BDF_CSV = Plugin(
    table_parser=DelimTxtParser(
        normalizer=BDF_NORMALIZER,
        unique_exts=frozenset({".bdf.csv"}),
    )
)

BDF_PARQUET = Plugin(table_parser=ParquetParser(normalizer=BDF_NORMALIZER))


PLUGINS: dict[str, Plugin] = PluginDict(
    {
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
        "neware_nda": NEWARE_NDA,
        "bdf_csv": BDF_CSV,
        "bdf_parquet": BDF_PARQUET,
    }
)


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------


def _ext_from_url(url: str) -> str:
    """Return the file extension from a URL path, parsed from the string alone (no I/O).

    Walks path segments right-to-left so URLs like ``.../file.csv/content`` resolve
    to ``.csv`` rather than empty.

    Args:
        url: HTTP(S) URL to extract an extension from.

    Returns:
        Lowercased extension including the dot, or ``""`` if none found.
    """
    from urllib.parse import urlparse

    segments = [s for s in urlparse(url).path.split("/") if s]
    for segment in reversed(segments):
        suffix = Path(segment).suffix
        if suffix:
            return suffix.lower()
    return ""


def detect_from_magic_bytes(
    path: str | Path,
    cands: dict[str, Plugin] = PLUGINS,
) -> dict[str, Plugin]:
    """Return plugins from ``cands`` whose table parser's head-byte signature matches ``path``.

    Defaults to :data:`PLUGINS`. Non-text (``is_text=False``) parsers are tried first;
    text parsers (e.g. ``DelimTxtParser``) are only included if no non-text parser
    matched, and even then only if their own (gated) ``matches_magic_bytes`` passes —
    this is what stops an unrecognised binary file from silently falling through to
    ``DelimTxtParser``.

    Args:
        path: Local file path or URL to check.
        cands: Candidate plugins to filter; defaults to PLUGINS.

    Returns:
        Dictionary of plugins whose table parser's magic bytes matched.

    Raises:
        ValueError: If no candidate's magic bytes matched.
    """
    head = read_head(resolve_source(path))
    binary = {
        id_: p for id_, p in cands.items() if not p.table_parser.is_text and p.table_parser.matches_magic_bytes(head)
    }
    if binary:
        return binary
    text = {id_: p for id_, p in cands.items() if p.table_parser.is_text and p.table_parser.matches_magic_bytes(head)}
    if not text:
        raise ValueError(f"no candidate's magic bytes matched {path!r}")
    return text


def detect_from_ext_or_magic_bytes(
    path: str | Path,
    cands: dict[str, Plugin] = PLUGINS,
) -> dict[str, Plugin]:
    """Return plugins from ``cands`` whose table parser handles the extension of ``path``.

    Defaults to :data:`PLUGINS`. The extension is read from the path/URL string alone
    (no network I/O, no local file access). Falls back to :func:`detect_from_magic_bytes`
    — which does fetch/read the file — when ``path`` has no extension, or no candidate
    handles the extension found.

    Args:
        path: Local file path or URL to check.
        cands: Candidate plugins to filter; defaults to PLUGINS.

    Returns:
        Dictionary of plugins whose table parser matches the extension or magic bytes.

    Raises:
        ValueError: If neither the extension nor magic bytes match any candidate.
    """
    path_str = str(path)
    ext = _ext_from_url(path_str) if is_url(path_str) else "".join(Path(path).suffixes).lower()
    if ext:
        matched = {id_: p for id_, p in cands.items() if p.table_parser.matches_ext(ext)}
        if matched:
            return matched
    return detect_from_magic_bytes(path, cands)


def detect_from_metadata(
    path: str | Path,
    cands: dict[str, Plugin] = PLUGINS,
) -> dict[str, Plugin]:
    """Return plugins from ``cands`` whose metadata parser matches ``path``.

    Defaults to :data:`PLUGINS`. When no candidate matches (file has no identifying
    preamble), returns ``cands`` unchanged so the pipeline continues to column scoring.

    Args:
        path: Local file path or URL to check.
        cands: Candidate plugins to filter; defaults to PLUGINS.

    Returns:
        Dictionary of plugins whose metadata parser matches the file, or all cands if none match.
    """
    matched = {id_: p for id_, p in cands.items() if p.metadata_parser.matches(path)}
    return matched if matched else cands


def detect_from_columns(
    path: str | Path,
    cands: dict[str, Plugin] = PLUGINS,
) -> tuple[str, Plugin]:
    """Return ``(plugin_id, Plugin)`` for the highest-scoring candidate on ``path``'s column headers.

    Defaults to :data:`PLUGINS`. Raises :exc:`ValueError` if no candidate scores above
    zero, or if the top score is tied between multiple candidates.

    Args:
        path: Local file path or URL to check.
        cands: Candidate plugins to score; defaults to PLUGINS.

    Returns:
        Tuple of (plugin_id, Plugin) for the best-scoring candidate.

    Raises:
        ValueError: If no candidate scores above zero or multiple candidates tie.
    """
    scored = {id_: p.table_parser.normalizer_score(path) for id_, p in cands.items()}
    best_score = max(scored.values(), default=0)
    if best_score == 0:
        raise ValueError(f"no candidate scored above zero on column headers for {path!r}")
    winners = {id_: p for id_, p in cands.items() if scored[id_] == best_score}
    if len(winners) > 1:
        raise ValueError(f"ambiguous match for {path!r}: {', '.join(winners)}")
    return next(iter(winners.items()))


def detect(path: str | Path) -> tuple[str, Plugin]:
    """Resolve ``(plugin_id, Plugin)`` for ``path`` (local file or URL).

    Calls :func:`detect_from_ext_or_magic_bytes` → :func:`detect_from_metadata` →
    :func:`detect_from_columns` in sequence, returning early after any stage that
    narrows candidates to exactly one.

    Args:
        path: Local file path or URL to detect plugin for.

    Returns:
        Tuple of (plugin_id, Plugin) for the detected file format.

    Raises:
        ValueError: If detection fails at any stage.
    """
    cands = detect_from_ext_or_magic_bytes(path)
    if len(cands) == 1:
        return next(iter(cands.items()))
    cands = detect_from_metadata(path, cands)
    if len(cands) == 1:
        return next(iter(cands.items()))
    return detect_from_columns(path, cands)


def list_sources() -> list[str]:
    """Return the list of registered plugin IDs.

    Returns:
        List of all registered plugin identifiers.
    """
    return list(PLUGINS)


# ---------------------------------------------------------------------------
# Plugin config file load/dump
# ---------------------------------------------------------------------------


def _is_yaml_path(path: str | Path) -> bool:
    """Return True if ``path``'s extension is ``.yaml`` or ``.yml``.

    Args:
        path: File path to check.

    Returns:
        True if the suffix indicates a YAML file.
    """
    return Path(path).suffix.lower() in (".yaml", ".yml")


def _require_yaml() -> None:
    """Raise if PyYAML is not installed.

    Raises:
        ImportError: If PyYAML is not available, chained from the original import error.
    """
    if not _HAS_YAML:
        raise ImportError(
            "YAML plugin files require PyYAML. Install with `pip install PyYAML`."
        ) from _YAML_IMPORT_ERROR


def load_plugins(path: str | Path) -> dict[str, Plugin]:
    """Load a ``{id: Plugin}`` dict from a JSON or YAML file.

    Dispatches on file extension: ``.yaml``/``.yml`` parse via ``yaml.safe_load``
    (requires PyYAML); any other extension parses as JSON.

    Args:
        path: Path to the plugin definitions file.

    Returns:
        Dictionary mapping plugin id to validated Plugin.

    Raises:
        ImportError: If the path is YAML and PyYAML is not installed.
        pydantic.ValidationError: If a plugin entry fails validation.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if _is_yaml_path(path):
        _require_yaml()
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return {id_: Plugin.model_validate(p) for id_, p in (data or {}).items()}


def dump_plugins(plugins: dict[str, Plugin], path: str | Path) -> None:
    """Serialize a ``{id: Plugin}`` dict to a JSON or YAML file.

    Dispatches on file extension: ``.yaml``/``.yml`` writes via ``yaml.safe_dump``
    (requires PyYAML); any other extension writes JSON. Fields left at their
    default (``None``) are omitted from the output to keep files readable.

    Args:
        plugins: Dictionary mapping plugin id to Plugin instance.
        path: Output file path.

    Raises:
        ImportError: If the path is YAML and PyYAML is not installed.
    """
    path = Path(path)
    data = {id_: p.model_dump(mode="json", exclude_none=True) for id_, p in plugins.items()}
    if _is_yaml_path(path):
        _require_yaml()
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
