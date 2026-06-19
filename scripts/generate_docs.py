"""Generate ontology-derived documentation tables.

The bundled ontology snapshot (src/bdf/data/bdf-ontology-snapshot.ttl) is the
single source of truth for BDF quantities. This script renders the
Required/Recommended/Optional quantity tables and injects them into the
regions of README.md bounded by marker comments:

    <!-- BEGIN GENERATED: bdf-terms-required -->
    ...replaced content...
    <!-- END GENERATED: bdf-terms-required -->

Everything outside the markers is left untouched. Do not edit the generated
regions by hand: change the ontology, update the snapshot, and re-run.

Usage:
    python scripts/generate_docs.py          # rewrite files in place
    python scripts/generate_docs.py --check  # exit 1 if any file is stale
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bdf.spec import COLUMN_ONTOLOGY, Quantity  # noqa: E402

OBLIGATION_LEVELS = ("required", "recommended", "optional")
TABLE_HEADER = "| Preferred Label | Machine-readable name | IRI | Description |\n|---|---|---|---|"


def _escape_cell(text: str) -> str:
    """Make *text* safe inside a Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def _description(q: Quantity) -> str:
    """Short table description: schema:description, else first definition sentence."""
    if q.description:
        return q.description
    first = q.definition.split(". ", 1)[0].strip()
    return f"{first}." if first and not first.endswith(".") else first


def _table_for_level(level: str) -> str:
    rows = sorted(
        (q for _, q in COLUMN_ONTOLOGY if q.obligation == level and not q.deprecated),
        key=lambda q: q.formatted_label,
    )
    lines = [TABLE_HEADER]
    for q in rows:
        lines.append(
            f"| {_escape_cell(q.formatted_label)} "
            f"| `{q.mr_name}` "
            f"| [{q.iri}]({q.iri}) "
            f"| {_escape_cell(_description(q))} |"
        )
    return "\n".join(lines)


def _region_content(level: str, stamp: str) -> str:
    table = _table_for_level(level)
    if level != "optional":
        return f"{stamp}\n{table}"
    # The optional table is by far the largest; keep the README scannable by
    # rendering it collapsed (blank lines around the table are required for
    # GitHub to render Markdown inside <details>).
    count = sum(1 for _, q in COLUMN_ONTOLOGY if q.obligation == "optional" and not q.deprecated)
    return (
        f"{stamp}\n"
        "<details>\n"
        f"<summary><b>{count} optional quantities</b> &mdash; click to expand</summary>\n"
        "\n"
        f"{table}\n"
        "\n"
        "</details>"
    )


def _generated_regions() -> dict[str, str]:
    stamp = (
        f"<!-- Generated from BDF ontology {COLUMN_ONTOLOGY.ontology_version} "
        "by scripts/generate_docs.py - do not edit by hand. -->"
    )
    return {f"bdf-terms-{level}": _region_content(level, stamp) for level in OBLIGATION_LEVELS}


def _inject(text: str, region: str, content: str, path: Path) -> str:
    pattern = re.compile(
        rf"(<!-- BEGIN GENERATED: {re.escape(region)} -->)\n?.*?\n?(<!-- END GENERATED: {re.escape(region)} -->)",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit(
            f"ERROR: marker region '{region}' not found in {path}. Add BEGIN/END GENERATED comments before running."
        )
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(2)}", text)


TARGET_FILES = [REPO_ROOT / "README.md"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if any generated region is stale.",
    )
    args = parser.parse_args()

    stale = []
    for path in TARGET_FILES:
        raw = path.read_bytes().decode("utf-8")
        eol = "\r\n" if "\r\n" in raw else "\n"
        current = raw.replace("\r\n", "\n")
        regenerated = current
        for region, content in _generated_regions().items():
            regenerated = _inject(regenerated, region, content, path)

        if regenerated == current:
            print(f"  ok    {path.relative_to(REPO_ROOT)}")
            continue

        if args.check:
            stale.append(path)
            diff = difflib.unified_diff(
                current.splitlines(keepends=True),
                regenerated.splitlines(keepends=True),
                fromfile=str(path),
                tofile=f"{path} (regenerated)",
            )
            sys.stdout.writelines(list(diff)[:80])
        else:
            path.write_bytes(regenerated.replace("\n", eol).encode("utf-8"))
            print(f"  wrote {path.relative_to(REPO_ROOT)}")

    if stale:
        print(
            f"\nERROR: {len(stale)} file(s) out of sync with the ontology snapshot.\n"
            "Regenerate with: python scripts/generate_docs.py"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
