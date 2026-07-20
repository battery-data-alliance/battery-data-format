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
from bdf.validate import validate_df

# Reuse the scorecard primitives so the record's audit matches the docs scorecards.
from build_scorecards import _mapping_and_unmapped, _stats, _time_scale_check  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO / "docs" / "examples" / "reference" / "datasets.json"

RAW_RECORD_DOI = "10.5281/zenodo.18986774"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stem(bdf_file: str) -> str:
    """'X.bdf.csv' -> 'X' (strip the .bdf.<ext> tail)."""
    return bdf_file.rsplit(".bdf.", 1)[0]


PLOTS_DIR = REPO / "docs" / "_static" / "scorecards"
_RANK = {"PASS": 0, "WARN": 1, "FAIL": 2}
_BADGE = {"PASS": "#1a7f37", "WARN": "#9a6700", "FAIL": "#cf222e"}


def _findings(card: dict) -> list[str]:
    out = []
    if card.get("time_scale"):
        out.append(f"Time-scale cross-check: {card['time_scale']}")
    for d in card.get("derived_issues", []):
        out.append(f"Derived-column: {d}")
    return out


def _write_scorecard_html(entries: list[dict], out_path: Path) -> None:
    """Render a self-contained scorecard report (plots embedded as data URIs)."""
    import base64
    import html

    esc = html.escape
    by_plugin: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("_card"):
            by_plugin.setdefault(e["plugin"], []).append(e)

    rows = []
    for plugin, es in sorted(by_plugin.items()):
        cards = [e["_card"] for e in es]
        worst = max((c["verdict"] for c in cards), key=lambda v: _RANK.get(v, 0))
        nf = sum(len(_findings(c)) for c in cards)
        rows.append(
            f'<tr><td><a href="#{esc(plugin)}">{esc(plugin)}</a></td>'
            f'<td><span class="b" style="background:{_BADGE[worst]}">{worst}</span></td>'
            f"<td>{len(cards)}</td><td>{nf or '&mdash;'}</td></tr>"
        )

    sections = []
    for plugin, es in sorted(by_plugin.items()):
        cards = []
        for e in es:
            c = e["_card"]
            findings = _findings(c)
            plot = ""
            png = PLOTS_DIR / (_stem(e["bdf_file"]) + ".png")
            if c.get("plot") and png.exists():
                b64 = base64.b64encode(png.read_bytes()).decode()
                plot = f'<img alt="overview" src="data:image/png;base64,{b64}">'
            fl = (
                '<ul class="find">' + "".join(f"<li>{esc(x)}</li>" for x in findings) + "</ul>"
                if findings
                else '<p class="ok">No findings.</p>'
            )
            maprows = "".join(
                f"<tr><td><code>{esc(m['native'])}</code></td><td><code>{esc(m['bdf'])}</code></td>"
                f"<td>{esc(str(m['conversion']))}</td></tr>"
                for m in c["mapping"]
            )
            dropped = ", ".join(f"<code>{esc(x) or '&lt;empty&gt;'}</code>" for x in c["unmapped_native_columns"])
            cards.append(
                f'<div class="card"><h3>{esc(_stem(e["bdf_file"]))} '
                f'<span class="b" style="background:{_BADGE[c["verdict"]]}">{c["verdict"]}</span></h3>'
                f'<p class="meta">{esc(e.get("notes", ""))}</p>{plot}{fl}'
                f'<details><summary>Column mapping &mdash; {c["mapped_columns"]}/{c["native_columns"]} mapped, '
                f"{len(c['unmapped_native_columns'])} dropped</summary>"
                f'<table class="map"><tr><th>Native</th><th>BDF</th><th>Conversion</th></tr>{maprows}</table>'
                + (f'<p class="drop"><b>Dropped:</b> {dropped}</p>' if dropped else "")
                + "</details></div>"
            )
        sections.append(f'<section id="{esc(plugin)}"><h2>{esc(plugin)}</h2>{"".join(cards)}</section>')

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BDF reference dataset scorecards</title>
<style>
:root{{--fg:#1f2328;--mut:#59636e;--line:#d1d9e0;--bg:#fff;--card:#f6f8fa}}
@media(prefers-color-scheme:dark){{:root{{--fg:#e6edf3;--mut:#9198a1;--line:#3d444d;--bg:#0d1117;--card:#151b23}}}}
*{{box-sizing:border-box}}body{{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--fg);
background:var(--bg);margin:0;padding:2.5rem 1.25rem;max-width:60rem;margin:auto}}
h1{{font-size:1.6rem;margin:0 0 .3rem}}h2{{font-size:1.2rem;margin:2.2rem 0 .8rem;padding-bottom:.3rem;
border-bottom:2px solid var(--line)}}h3{{font-size:1rem;margin:0 0 .4rem;display:flex;gap:.5rem;align-items:center}}
p.lede{{color:var(--mut);margin:0 0 1.6rem}}.b{{color:#fff;font-size:.72rem;font-weight:700;padding:.1rem .5rem;
border-radius:1rem;letter-spacing:.03em}}table{{border-collapse:collapse;width:100%;margin:.4rem 0;font-size:.9rem}}
th,td{{text-align:left;padding:.35rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600}}table.sum td:first-child a{{font-weight:600}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:.6rem;padding:1rem 1.2rem;margin:.9rem 0}}
.card img{{max-width:100%;border-radius:.3rem;margin:.3rem 0}}p.meta{{color:var(--mut);margin:.1rem 0 .6rem;font-size:.9rem}}
ul.find{{margin:.3rem 0;padding-left:1.1rem}}ul.find li{{color:#cf222e}}p.ok{{color:#1a7f37;margin:.3rem 0}}
code{{font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--line);padding:.1rem .3rem;border-radius:.2rem}}
details{{margin-top:.5rem}}summary{{cursor:pointer;color:var(--mut);font-size:.88rem}}
.map{{overflow-x:auto;display:block}}p.drop{{font-size:.85rem;color:var(--mut)}}a{{color:#0969da}}
</style></head><body>
<h1>Battery Data Format &mdash; reference dataset scorecards</h1>
<p class="lede">Each dataset is re-converted from its raw vendor export and audited: verdict, column mapping,
and any cross-check findings. One row per plugin (converter). Generated with bdf {esc(bdf.__version__)}.</p>
<table class="sum"><tr><th>Plugin</th><th>Verdict</th><th>Datasets</th><th>Findings</th></tr>{''.join(rows)}</table>
{''.join(sections)}
</body></html>"""
    out_path.write_text(doc, encoding="utf-8")


def _generate(entry: dict, out_dir: Path, formats: list[str]) -> tuple[dict[str, Path], dict]:
    """Convert the entry's raw source into each format, and score the published artifact.

    The card audits the fresh conversion that is actually written to the bundle
    (mapping, dropped columns, derived-identity findings, time-unit cross-check),
    NOT the git-committed copy -- so it describes exactly what the record ships.
    """
    raw = entry["raw_files"][0]
    local = fetch_url(raw["url"], filename=raw["filename"])
    stem = _stem(entry["bdf_file"])
    outs: dict[str, Path] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw_df, _ = bdf.read(local, plugin=entry["plugin"], normalize=False, validate=False, lazy=False)
        df, _meta = bdf.read(local, plugin=entry["plugin"], validate=True, lazy=False)
        pdf = df.to_pandas()  # pandas at the boundary: accepted by pre- and post-0.2.0 save
        rep = validate_df(pdf, report=False, raise_on_error=False)
        for fmt in formats:
            out = out_dir / f"{stem}.bdf.{fmt}"
            bdf.save(pdf, out)
            outs[fmt] = out

    mapping, unmapped = _mapping_and_unmapped(entry["plugin"], list(raw_df.columns))
    derived = list(rep["derived"]["issues"])
    time_scale = _time_scale_check(df)
    if time_scale:
        verdict = "FAIL"
    elif derived and not entry.get("deliberate_data_bugs"):
        verdict = "WARN"
    else:
        verdict = "PASS"
    card = {
        "verdict": verdict,
        "native_columns": len(raw_df.columns),
        "mapped_columns": len(mapping),
        "mapping": mapping,
        "unmapped_native_columns": unmapped,
        "stats": _stats(df),
        "derived_issues": derived,
        "time_scale": time_scale,
        "plot": _stem(entry["bdf_file"]) + ".png",
    }
    return outs, card


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None, help="Bundle output directory (required for generation).")
    parser.add_argument("--only", default=None, help="Only process entries whose bdf_file contains this substring.")
    parser.add_argument("--formats", default="csv,parquet", help="Comma-separated on-disk formats to emit (default csv,parquet).")
    parser.add_argument("--record-id", default=None, help="Published Zenodo record id: back-fill URLs/DOI, no generation.")
    args = parser.parse_args()
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    selected = [e for e in manifest["datasets"] if not args.only or args.only in e["bdf_file"]]
    # Entries held from this record version (e.g. awaiting a fix) stay in the repo
    # manifest and the docs scorecards, but are excluded from the published bundle.
    held = [e for e in selected if e.get("hold_from_record")]
    entries = [e for e in selected if not e.get("hold_from_record")]
    for e in held:
        e["artifact"] = None  # drop any stale checksums for a held dataset

    if args.record_id:
        base = f"https://zenodo.org/records/{args.record_id}/files"
        for e in entries:
            art = e.get("artifact")
            if not art:
                raise SystemExit(f"{e['bdf_file']}: no artifact block; run generation first")
            art["record_id"] = args.record_id
            art["doi"] = f"10.5281/zenodo.{args.record_id}"
            for fmt, meta in art["formats"].items():
                meta["url"] = f"{base}/{meta['filename']}?download=1"
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"back-filled record {args.record_id} into {len(entries)} entries")
        return 0

    if not args.out:
        raise SystemExit("--out is required for generation")
    args.out.mkdir(parents=True, exist_ok=True)

    for e in entries:
        outs, e["_card"] = _generate(e, args.out, formats)
        e["artifact"] = {
            "generated_with": f"bdf {bdf.__version__}",
            "record_id": None,
            "doi": None,
            "formats": {
                fmt: {"filename": path.name, "sha256": _sha256(path), "bytes": path.stat().st_size, "url": None}
                for fmt, path in outs.items()
            },
        }
        sizes = "  ".join(f"{fmt}:{outs[fmt].stat().st_size:,}B" for fmt in formats)
        print(f"[ok] {_stem(e['bdf_file'])}  {sizes}")

    _write_scorecard_html(entries, args.out / "scorecards.html")
    print(f"wrote {args.out / 'scorecards.html'}")

    for e in entries:  # _card is transient (drives the HTML); keep it out of the manifest
        e.pop("_card", None)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    readme = [
        "This record contains the canonical BDF reference artifacts of the battery-data-format package: "
        f"real cycler measurements converted from the raw exports published at {RAW_RECORD_DOI}, "
        f"one faithful conversion per raw file (no derived quantities synthesized), in {' and '.join(f.upper() for f in formats)}. "
        "Both formats read identically via bdf.read; CSV is human-readable, Parquet preserves exact dtypes. "
        "One record version is published per bdf release. See scorecards.html for the per-plugin conversion audit. "
        "The machine-readable pairing (raw source, checksums, converter, provenance) is maintained in "
        "datasets.json in the battery-data-format repository.",
        "",
        "| Artifact | Format | Size / B | sha256 |",
        "|---|---|---|---|",
    ]
    for e in entries:
        for fmt, meta in e["artifact"]["formats"].items():
            readme.append(f"| {meta['filename']} | {fmt} | {meta['bytes']} | {meta['sha256']} |")
    if held:
        readme += ["", "Held from this version (pending a fix, included in a later version):", ""]
        for e in held:
            readme.append(f"- {_stem(e['bdf_file'])} — {e['hold_from_record']}")
    (args.out / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    meta = {
        "title": "Battery Data Format (BDF) canonical reference artifacts",
        "upload_type": "dataset",
        "description": readme[0],
        "creators": [
            {"name": "Clark, Simon", "orcid": "0000-0002-8758-6109"},
            {"name": "Hege, Gabe"},  # ORCID to be supplied
            {"name": "Kimbell, Graham", "orcid": "0000-0001-9610-3589"},
            {"name": "Holland, Tom", "orcid": "0009-0009-0670-1901"},
        ],
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
