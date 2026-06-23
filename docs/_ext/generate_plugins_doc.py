"""Sphinx extension: regenerate the Supported Plugins page before each build.

The ``bdf.plugins.PLUGINS`` dict is the single source of truth for which cycler
exports ``bdf.read`` can auto-detect and how each is parsed. This extension
renders a catalog of every plugin -- its table parser, metadata parser, and
full column synonym map (assumed synonyms flagged) -- and injects it into the
region of docs/plugins.rst bounded by marker comments:

    .. BEGIN GENERATED: plugin-catalog
    ...replaced content...
    .. END GENERATED: plugin-catalog

Everything outside the markers is left untouched. Do not edit the generated
region by hand: change the plugin definitions and let the next docs build
regenerate it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sphinx.util import logging  # noqa: E402

from bdf.metadata_parsers import JsonSidecarParser, TxtPreambleParser  # noqa: E402
from bdf.plugins import PLUGINS  # noqa: E402
from bdf.spec import COLUMN_ONTOLOGY  # noqa: E402
from bdf.table_normalizers import DateTimeSyn, ResolvedColumn, Syn  # noqa: E402

logger = logging.getLogger(__name__)

REGION = "plugin-catalog"
TARGET_FILE = REPO_ROOT / "docs" / "plugins.rst"

# Already-normalized BDF artifacts: their column names are the canonical BDF
# labels, so a synonym catalog would be self-referential noise. Omitted.
SKIP_PLUGINS = frozenset({"bdf_csv", "bdf_parquet"})


def _lit(text: str) -> str:
    """Render *text* as an RST inline literal (double backticks)."""
    return f"``{text}``"


def _field_label(mr_name: str) -> str:
    """Human label for a BDF mr_name, falling back to the mr_name itself."""
    quantity = COLUMN_ONTOLOGY.get(mr_name)
    return quantity.formatted_label if quantity is not None else mr_name


def _parser_config(table_parser) -> list[str]:
    """Non-default table-parser settings, excluding identity/normalizer/exts."""
    skip = {"kind", "normalizer", "unique_exts"}
    lines = []
    for name in sorted(table_parser.model_fields_set - skip):
        value = getattr(table_parser, name)
        lines.append(f"{name} = {_lit(str(value))}")
    return lines


def _table_parser_lines(table_parser) -> list[str]:
    """Bullet describing the table parser: kind, handled extensions, config."""
    exts = sorted(type(table_parser).base_exts | table_parser.unique_exts)
    parts = [f"**Table parser:** {_lit(table_parser.kind)} ({', '.join(_lit(e) for e in exts)})"]
    config = _parser_config(table_parser)
    if config:
        parts.append("; ".join(config))
    return ["- " + " -- ".join(parts)]


def _decode_magic(token: str | bytes) -> str:
    """Display form of a magic token (bytes shown via repr)."""
    return token if isinstance(token, str) else repr(token)


def _metadata_parser_lines(metadata_parser) -> list[str]:
    """Single bullet naming the metadata parser; details go in a dropdown."""
    if isinstance(metadata_parser, TxtPreambleParser):
        head = f"**Metadata parser:** {_lit('txt_preamble')} -- preamble of the data file"
        if metadata_parser.encoding != "utf-8":
            head += f" (decoded as {_lit(metadata_parser.encoding)})"
        return ["- " + head]
    if isinstance(metadata_parser, JsonSidecarParser):
        return [f"- **Metadata parser:** {_lit('json_sidecar')} -- adjacent ``.json`` file"]
    return ["- **Metadata parser:** none"]


def _metadata_dropdown(metadata_parser) -> list[str]:
    """Collapsible ``.. dropdown::`` with magic tokens and extracted-field rules.

    ``rules`` pairs each BDF metadata field name with a human description of how
    its value is found, rendered as a uniform sub-list so adding fields beyond
    ``start_time`` slots in without reshaping the surrounding text. Parsers with
    no extraction detail (e.g. ``none``) get no dropdown.
    """
    intro: list[str] = []
    if isinstance(metadata_parser, TxtPreambleParser):
        magic = ", ".join(_lit(_decode_magic(m)) for m in metadata_parser.magic)
        intro = [f"   - Identified by magic tokens: {magic}"]
        rules = [(name, f"regex {_lit(pattern.pattern)}") for name, pattern in metadata_parser.regex_patterns]
    elif isinstance(metadata_parser, JsonSidecarParser):
        rules = [
            (name, "JSON key(s): " + ", ".join(_lit(k) for k in keys)) for name, keys in metadata_parser.key_synonyms
        ]
    else:
        return []

    lines = ["", ".. dropdown:: Metadata extraction details", "", *intro]
    if rules:
        lines.extend(["   - Extracted metadata fields:", ""])
        lines.extend(f"     - {_lit(name)} -- {how}" for name, how in rules)
    lines.append("")
    return lines


def _synonym_bullet(syn: Syn | DateTimeSyn) -> str:
    """One bullet line for a single synonym, flagging assumed entries."""
    if isinstance(syn, DateTimeSyn):
        base, assumed = syn.syn.hdr, syn.syn.assumed
        fmts = ", ".join(_lit(f) for f in syn.fmts)
        line = f"- {_lit(base)} -- formats: {fmts}" if fmts else f"- {_lit(base)}"
    else:
        base, assumed = syn.hdr, syn.assumed
        line = f"- {_lit(base)}"
    return line + " *(assumed)*" if assumed else line


def _is_assumed(syn: Syn | DateTimeSyn) -> bool:
    return syn.syn.assumed if isinstance(syn, DateTimeSyn) else syn.assumed


def _synonym_dropdown(normalizer) -> list[str]:
    """Collapsible ``.. dropdown::`` listing every mapped BDF field's synonyms."""
    fields = list(normalizer)
    n_syns = sum(len(val) for _, val in fields if isinstance(val, tuple))
    n_assumed = sum(_is_assumed(s) for _, val in fields if isinstance(val, tuple) for s in val)
    n_resolved = sum(1 for _, val in fields if isinstance(val, ResolvedColumn))

    summary = f"Column synonyms -- {len(fields)} BDF fields, {n_syns} synonyms ({n_assumed} assumed)"
    if n_resolved:
        summary += f", {n_resolved} direct"
    lines = ["", f".. dropdown:: {summary}", ""]

    for mr_name, val in fields:
        lines.append(f"   **{_field_label(mr_name)}** -- {_lit(mr_name)}")
        lines.append("")
        if isinstance(val, ResolvedColumn):
            detail = f"   - Direct mapping from {_lit(val.source_header)}"
            if (val.scale, val.offset) != (1.0, 0.0):
                detail += f" (scale {val.scale}, offset {val.offset})"
            if val.datetime_fmts:
                detail += " -- formats: " + ", ".join(_lit(f) for f in val.datetime_fmts)
            lines.append(detail)
        else:
            lines.extend("   " + _synonym_bullet(syn) for syn in val)
        lines.append("")
    return lines


def _plugin_section(plugin_id: str, plugin) -> list[str]:
    """Full RST section: heading, table parser, synonym + metadata dropdowns."""
    lines = [_lit(plugin_id), "-" * (len(plugin_id) + 4), ""]
    lines.extend(_table_parser_lines(plugin.table_parser))
    lines.extend(_synonym_dropdown(plugin.table_parser.normalizer))
    lines.extend(_metadata_parser_lines(plugin.metadata_parser))
    lines.extend(_metadata_dropdown(plugin.metadata_parser))
    lines.append("")
    return lines


def _generated_content() -> str:
    """Render the full generated catalog for every plugin in registry order."""
    stamp = ".. Generated by docs/_ext/generate_plugins_doc.py from bdf.plugins.PLUGINS - do not edit by hand."
    blocks = [stamp, ""]
    for plugin_id, plugin in PLUGINS.items():
        if plugin_id in SKIP_PLUGINS:
            continue
        blocks.extend(_plugin_section(plugin_id, plugin))
    return "\n".join(blocks).rstrip()


def _inject(text: str, content: str) -> str:
    pattern = re.compile(
        rf"(\.\. BEGIN GENERATED: {re.escape(REGION)})\n?.*?\n?(\.\. END GENERATED: {re.escape(REGION)})",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit(
            f"ERROR: marker region '{REGION}' not found in {TARGET_FILE}. "
            "Add BEGIN/END GENERATED comments before running."
        )
    return pattern.sub(lambda m: f"{m.group(1)}\n\n{content}\n\n{m.group(2)}", text)


def regenerate() -> bool:
    """Rewrite the generated region of docs/plugins.rst. Returns True if changed."""
    raw = TARGET_FILE.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    current = raw.replace("\r\n", "\n")
    regenerated = _inject(current, _generated_content())

    if regenerated == current:
        return False

    TARGET_FILE.write_bytes(regenerated.replace("\n", eol).encode("utf-8"))
    return True


def _on_builder_inited(app) -> None:
    rel = TARGET_FILE.relative_to(REPO_ROOT)
    if regenerate():
        logger.info(f"generate_plugins_doc: regenerated {rel} from bdf.plugins.PLUGINS")
    else:
        logger.info(f"generate_plugins_doc: {rel} already in sync")


def setup(app):
    app.connect("builder-inited", _on_builder_inited)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
