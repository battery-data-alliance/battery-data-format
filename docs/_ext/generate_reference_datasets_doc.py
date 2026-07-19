"""Sphinx extension: regenerate the Reference Datasets page before each build.

``docs/examples/reference/datasets.json`` is the single source of truth pairing
every canonical BDF artifact in ``docs/examples/reference/`` with the raw vendor
export (on Zenodo) it was generated from. This extension renders that manifest
as a catalog -- one section per dataset with provenance, plugin, columns, and
any deliberate data bugs -- and injects it into the region of
docs/reference_datasets.rst bounded by marker comments:

    .. BEGIN GENERATED: reference-datasets
    ...replaced content...
    .. END GENERATED: reference-datasets

Everything outside the markers is left untouched. Do not edit the generated
region by hand: change datasets.json and let the next docs build regenerate it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sphinx.util import logging

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGION = "reference-datasets"
TARGET_FILE = REPO_ROOT / "docs" / "reference_datasets.rst"
MANIFEST = REPO_ROOT / "docs" / "examples" / "reference" / "datasets.json"


def _lit(text: str) -> str:
    """Render *text* as an RST inline literal (double backticks)."""
    return f"``{text}``"


def _mib(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MiB"


_VERDICT_BADGE = {
    "PASS": ":bdg-success:`PASS`",
    "WARN": ":bdg-warning:`WARN`",
    "FAIL": ":bdg-danger:`FAIL`",
}


def _summary_table(datasets: list[dict]) -> list[str]:
    """Render the at-a-glance scorecard summary table."""
    lines = [
        "Scorecard summary",
        "-----------------",
        "",
        "Each artifact is re-converted from its raw source and compared against the",
        "committed file by ``scripts/build_scorecards.py``. **FAIL** means the committed",
        "artifact disagrees with a fresh conversion (stale or produced by a buggy",
        "converter); **WARN** means undocumented derived-column findings; details are in",
        "each dataset's scorecard below.",
        "",
        ".. list-table::",
        "   :header-rows: 1",
        "   :widths: 8 46 10 12 12 12",
        "",
        "   * - Verdict",
        "     - Dataset",
        "     - Rows",
        "     - Mapped",
        "     - Dropped",
        "     - Findings",
    ]
    for e in datasets:
        card = e.get("scorecard")
        if not card:
            continue
        # mirror docutils' section-id generation: lowercase, non-alphanumeric runs -> single hyphen
        anchor = re.sub(r"[^a-z0-9]+", "-", e["bdf_file"].lower()).strip("-")
        n_findings = len(card["derived_issues"]) + (
            0 if card["artifact_vs_fresh"] == ["matches fresh conversion"] else 1
        ) + (1 if card.get("time_scale") else 0)
        lines.extend(
            [
                f"   * - {_VERDICT_BADGE.get(card['verdict'], card['verdict'])}",
                f"     - `{e['bdf_file']} <#{anchor}>`__",
                f"     - {card['stats'].get('rows', '?'):,}",
                f"     - {card['mapped_columns']}/{card['native_columns']}",
                f"     - {len(card['unmapped_native_columns'])}",
                f"     - {n_findings or '—'}",
            ]
        )
    lines.append("")
    return lines


def _scorecard_block(entry: dict) -> list[str]:
    """Render one dataset's scorecard: verdict, plot, checks, mapping, dropped columns."""
    card = entry.get("scorecard")
    if not card:
        return []
    lines: list[str] = []

    stats = card["stats"]
    stat_bits = [f"{stats.get('rows', 0):,} rows"]
    if "duration_hours" in stats:
        stat_bits.append(f"{stats['duration_hours']:,} h")
    for c in ("Voltage / V", "Current / A"):
        if c in stats:
            s = stats[c]
            stat_bits.append(f"{c}: [{s['min']}, {s['max']}]" + (f" ({s['nulls']} nulls)" if s["nulls"] else ""))
    lines.extend([f"{_VERDICT_BADGE.get(card['verdict'], card['verdict'])} {' — '.join(stat_bits)}", ""])

    if card.get("plot"):
        lines.extend([f".. image:: _static/scorecards/{card['plot']}", "   :width: 100%", ""])

    # finding strings may contain RST-active characters (e.g. |delta| pipes) — render as literals
    checks = []
    for f in card["artifact_vs_fresh"]:
        if f == "matches fresh conversion":
            checks.append(f"- ✓ raw-vs-artifact: {f}")
        else:
            checks.append(f"- ✗ raw-vs-artifact: {_lit(f)}")
    if card.get("time_scale"):
        lines.append(f"- **Time-scale cross-check:** {card['time_scale']}")
    if card["derived_issues"]:
        for issue in card["derived_issues"]:
            checks.append(f"- ✗ derived: {_lit(issue)}")
    else:
        checks.append("- ✓ derived-column consistency: no findings")
    lines.extend(checks)
    lines.append("")

    lines.extend(
        [
            f".. dropdown:: Column mapping ({card['mapped_columns']} mapped, "
            f"{len(card['unmapped_native_columns'])} native columns dropped)",
            "",
            "   .. list-table::",
            "      :header-rows: 1",
            "      :widths: 40 40 20",
            "",
            "      * - Native column",
            "        - BDF column",
            "        - Conversion",
        ]
    )
    for m in card["mapping"]:
        lines.extend(
            [
                f"      * - {_lit(m['native'])}",
                f"        - {_lit(m['bdf'])}",
                f"        - {m['conversion']}",
            ]
        )
    if card["unmapped_native_columns"]:
        lines.extend(
            [
                "",
                "   **Dropped (no BDF mapping):** "
                + ", ".join(_lit(c) if c else "``<empty header>``" for c in card["unmapped_native_columns"]),
            ]
        )
    lines.append("")
    return lines


def _dataset_section(entry: dict) -> list[str]:
    """Render one dataset pairing as an RST section."""
    title = entry["bdf_file"]
    lines = [title, "-" * len(title), ""]

    if entry.get("notes"):
        lines.extend([entry["notes"], ""])

    if entry.get("deliberate_data_bugs"):
        lines.extend(
            [
                ".. warning::",
                "",
                f"   **Deliberately imperfect data.** {entry['deliberate_data_bugs']}",
                "",
            ]
        )

    lines.extend(
        [
            f"- **Provider:** {entry['provider']}",
            f"- **Plugin:** {_lit(entry['plugin'])}",
            f"- **Rows:** {entry['rows']:,}",
            f"- **BDF artifact:** {_lit('docs/examples/reference/' + entry['bdf_file'])} "
            f"(sha256 {_lit(entry['bdf_sha256'][:12] + '...')})",
        ]
    )
    for raw in entry["raw_files"]:
        lines.append(
            f"- **Raw export:** `{raw['filename']} <{raw['url']}>`__ ({_mib(raw['size_bytes'])}, {raw['checksum']})"
        )
    lines.append("")

    lines.append(f"- **Columns:** {', '.join(_lit(c) for c in entry['columns'])}")
    lines.append("")
    lines.extend(_scorecard_block(entry))
    return lines


def _generated_content() -> str:
    """Render the full catalog from the datasets.json manifest."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stamp = (
        ".. Generated by docs/_ext/generate_reference_datasets_doc.py from "
        "docs/examples/reference/datasets.json - do not edit by hand."
    )
    blocks: list[str] = [stamp, ""]

    if any(e.get("scorecard") for e in manifest["datasets"]):
        blocks.extend(_summary_table(manifest["datasets"]))

    for entry in manifest["datasets"]:
        blocks.extend(_dataset_section(entry))

    unconverted = manifest.get("unconverted_raw_files", [])
    if unconverted:
        title = "Raw files without a committed BDF equivalent"
        blocks.extend([title, "-" * len(title), ""])
        for item in unconverted:
            blocks.append(f"- `{item['raw_file']} <{item['url']}>`__ ({_mib(item['size_bytes'])}) -- {item['reason']}")
        blocks.append("")

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
    """Rewrite the generated region of docs/reference_datasets.rst. Returns True if changed."""
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
        logger.info(f"generate_reference_datasets_doc: regenerated {rel} from datasets.json")
    else:
        logger.info(f"generate_reference_datasets_doc: {rel} already in sync")


def setup(app):
    app.connect("builder-inited", _on_builder_inited)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
