"""Regenerate all reference artifacts and assemble the Zenodo artifact-record bundle.

One-pass publication workflow (run after the normalizer/io changes for a release
have merged, so artifacts are generated exactly once per release):

1. For every entry in ``datasets.json``: fetch the raw source through the
   content-addressed cache, convert with the entry's plugin via the current
   ``bdf`` code, and write the artifact into the bundle directory.
2. Compute sha256 and size for each artifact and write them into the manifest
   (``artifact`` block per entry). The Zenodo record id/DOI fields stay null
   until the record is published; ``--record-id`` back-fills them and the
   download URLs afterwards.
3. Emit the record's ``metadata.json`` and ``README.md`` alongside the bundle,
   ready for upload.

The bundle directory is scratch output and must not be committed.

Usage:
    python scripts/prepare_artifact_record.py --out /tmp/bdf-artifact-bundle
    python scripts/prepare_artifact_record.py --only FZJ --out /tmp/bundle
    python scripts/prepare_artifact_record.py --record-id 12345678   # after publishing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from pathlib import Path

import bdf
from bdf.fetch import fetch_url

REPO = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO / "docs" / "examples" / "reference" / "datasets.json"

RAW_RECORD_DOI = "10.5281/zenodo.18986774"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _generate(entry: dict, out_dir: Path) -> Path:
    """Convert the entry's raw source to its canonical artifact in out_dir."""
    raw = entry["raw_files"][0]
    local = fetch_url(raw["url"], filename=raw["filename"])
    out = out_dir / entry["bdf_file"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df, _meta = bdf.read(local, plugin=entry["plugin"], validate=True, lazy=False)
        bdf.save(df.to_pandas(), out)  # pandas at the boundary: accepted by both the pre- and post-0.2.0 save
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None, help="Bundle output directory (required for generation).")
    parser.add_argument("--only", default=None, help="Only process entries whose bdf_file contains this substring.")
    parser.add_argument("--record-id", default=None, help="Published Zenodo record id: back-fill URLs/DOI, no generation.")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = [e for e in manifest["datasets"] if not args.only or args.only in e["bdf_file"]]

    if args.record_id:
        base = f"https://zenodo.org/records/{args.record_id}/files"
        for e in entries:
            art = e.get("artifact")
            if not art:
                raise SystemExit(f"{e['bdf_file']}: no artifact block; run generation first")
            art["record_id"] = args.record_id
            art["doi"] = f"10.5281/zenodo.{args.record_id}"
            art["url"] = f"{base}/{e['bdf_file']}?download=1"
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"back-filled record {args.record_id} into {len(entries)} entries")
        return 0

    if not args.out:
        raise SystemExit("--out is required for generation")
    args.out.mkdir(parents=True, exist_ok=True)

    for e in entries:
        out = _generate(e, args.out)
        e["artifact"] = {
            "filename": e["bdf_file"],
            "sha256": _sha256(out),
            "bytes": out.stat().st_size,
            "generated_with": f"bdf {bdf.__version__}",
            "record_id": None,
            "doi": None,
            "url": None,
        }
        print(f"[ok] {e['bdf_file']}  {e['artifact']['bytes']:>10,} B  {e['artifact']['sha256'][:12]}")

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    readme = [
        "This record contains the canonical BDF reference artifacts of the battery-data-format package: "
        f"real cycler measurements converted from the raw exports published at {RAW_RECORD_DOI}, "
        "one artifact per raw file, generated with the released bdf version named per file below. "
        "One record version is published per bdf release. The machine-readable pairing (raw source, checksum, "
        "converter, per-file provenance) is maintained in datasets.json in the battery-data-format repository.",
        "",
        "| Artifact | Size / B | sha256 | Generated with |",
        "|---|---|---|---|",
    ]
    for e in entries:
        a = e["artifact"]
        readme.append(f"| {a['filename']} | {a['bytes']} | {a['sha256']} | {a['generated_with']} |")
    (args.out / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    meta = {
        "title": "Battery Data Format (BDF) canonical reference artifacts",
        "upload_type": "dataset",
        "description": readme[0],
        "creators": [{"name": "Battery Data Alliance"}],
        "license": "cc-by-4.0",
        "related_identifiers": [
            {"relation": "isDerivedFrom", "identifier": RAW_RECORD_DOI},
            {"relation": "isSupplementTo", "identifier": "https://github.com/battery-data-alliance/battery-data-format"},
        ],
    }
    (args.out / "zenodo_metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"bundle ready in {args.out} ({len(entries)} artifacts + README.md + zenodo_metadata.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
