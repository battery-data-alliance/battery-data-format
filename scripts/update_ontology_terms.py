"""Build the offline suggestion index from a pinned BattINFO mapping file.

Only this maintainer command uses the network. Normal BDF use reads the generated
JSON resource. Pass --source FILE to reproduce a build from a downloaded source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_REF = "567972ecacc604feb77c0d38e2a78e3daf2ae672"
SOURCE_PATH = "assets/mappings/domain-battery/property_map.curated.json"
OUTPUT = Path(__file__).resolve().parents[1] / "src/bdf/data/ontology-terms.json"

# BDF-maintained dimensional hints, not constraints supplied by BattINFO's
# mapping table. Keep these limited to unambiguous elementary quantities.
# They reject dimensionally incompatible candidates; they never convert values.
REFERENCE_UNITS = {
    "Thickness": "m",
    "Length": "m",
    "Width": "m",
    "Height": "m",
    "Diameter": "m",
    "Mass": "kg",
    "Volume": "m**3",
}


def build_index(raw: bytes, ref: str) -> dict:
    """Group curated authoring keys by IRI without guessing additional synonyms."""
    mapping = json.loads(raw)
    terms: dict[str, dict] = {}
    for entry in mapping["mappings"]:
        if entry.get("status") != "curated":
            continue
        iri = entry["class_iri"]
        label = entry["class_pref_label"]
        term = terms.setdefault(
            iri,
            {
                "iri": iri,
                "label": label,
                "names": [],
                "definition": entry.get("definition"),
                "reference_unit": REFERENCE_UNITS.get(label),
            },
        )
        term["names"] = sorted(set(term["names"]) | {entry["key"], label, entry.get("class_label", label)})
    return {
        "format_version": 1,
        "source": {
            "repository": "https://github.com/BIG-MAP/BattINFO",
            "ref": ref,
            "path": SOURCE_PATH,
            "mapping_version": mapping["version"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "license": "Apache-2.0",
        },
        "scope": "Curated BattINFO property mappings; not the entire ontology.",
        "unit_hints": "BDF-maintained dimensional hints for elementary geometry and mass only.",
        "terms": sorted(terms.values(), key=lambda t: (t["label"], t["iri"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=DEFAULT_REF, help="Exact upstream commit (40 hexadecimal characters)")
    parser.add_argument("--source", type=Path, help="Read a previously downloaded mapping instead of fetching it")
    parser.add_argument("--check", action="store_true", help="Fail if the generated index differs")
    args = parser.parse_args()
    if len(args.ref) != 40 or any(c not in "0123456789abcdef" for c in args.ref):
        parser.error("--ref must be an exact commit, not a moving branch or tag")
    if args.source:
        raw = args.source.read_bytes()
    else:
        import requests

        response = requests.get(
            f"https://raw.githubusercontent.com/BIG-MAP/BattINFO/{args.ref}/{SOURCE_PATH}", timeout=30
        )
        response.raise_for_status()
        raw = response.content
    rendered = json.dumps(build_index(raw, args.ref), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Ontology suggestion index differs; run scripts/update_ontology_terms.py")
        print("Ontology suggestion index is reproducible.")
    else:
        OUTPUT.write_text(rendered, encoding="utf-8")
        print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
