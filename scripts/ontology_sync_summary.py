"""Summarize the differences between two ontology snapshots as Markdown.

Used by the sync-ontology workflow to give the auto-PR a human-reviewable
body instead of a raw TTL diff.

Usage:
    python scripts/ontology_sync_summary.py OLD.ttl NEW.ttl
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # arrows/emoji on Windows consoles

from rdflib import Graph  # noqa: E402

from bdf.spec import ColumnOntology  # noqa: E402


def _load(path: str) -> ColumnOntology:
    g = Graph()
    g.parse(path, format="turtle")
    return ColumnOntology.from_graph(g)


def _required_set(onto: ColumnOntology) -> set[str]:
    return {name for name, q in onto if q.required and not q.deprecated}


def main(old_path: str, new_path: str) -> int:
    old, new = _load(old_path), _load(new_path)
    old_names = {name for name, _ in old}
    new_names = {name for name, _ in new}
    common = old_names & new_names

    lines = [
        f"## Ontology snapshot update: {old.ontology_version or '?'} → "
        f"{new.ontology_version or '?'}",
        "",
    ]

    old_req, new_req = _required_set(old), _required_set(new)
    if old_req != new_req:
        lines += [
            "### ⚠️ Required-column set changed — affects `validate_df()`",
            "",
            f"- previously required: `{sorted(old_req)}`",
            f"- now required: `{sorted(new_req)}`",
            "",
            "Update `EXPECTED_REQUIRED` in `tests/unit/test_spec_ontology_fields.py` "
            "in this PR after confirming the change is intentional.",
            "",
        ]

    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    if added:
        lines.append("### Added terms")
        lines += [f"- `{n}` ({new[n].obligation or 'no obligation'})" for n in added]
        lines.append("")
    if removed:
        lines.append("### Removed terms")
        lines += [f"- `{n}`" for n in removed]
        lines.append("")

    newly_deprecated = sorted(n for n in common if new[n].deprecated and not old[n].deprecated)
    if newly_deprecated:
        lines.append("### Newly deprecated")
        lines += [f"- `{n}`" for n in newly_deprecated]
        lines.append("")

    obligation_changes = sorted(
        n for n in common if old[n].obligation != new[n].obligation
    )
    if obligation_changes:
        lines.append("### Obligation changes")
        lines += [
            f"- `{n}`: {old[n].obligation or '(none)'} → {new[n].obligation or '(none)'}"
            for n in obligation_changes
        ]
        lines.append("")

    wording = sorted(
        n
        for n in common
        if (old[n].definition, old[n].description) != (new[n].definition, new[n].description)
    )
    if wording:
        lines.append("### Definition/description wording changed")
        lines += [f"- `{n}`" for n in wording]
        lines.append("")

    if len(lines) == 2:
        lines.append("No term-level changes detected (metadata-only update).")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
