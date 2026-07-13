"""Build per-dataset scorecards for the canonical reference examples.

For every entry in ``docs/examples/reference/datasets.json`` this script fetches the
raw vendor export (through the content-addressed ``bdf.fetch`` cache), re-converts it
with the current reader, and compares the result against the committed BDF artifact.
The outcome is written back into the manifest under a per-entry ``scorecard`` key and
rendered by the Reference Datasets docs page:

* ``verdict`` — PASS / WARN / FAIL for the committed artifact:
    - FAIL: the artifact disagrees with a fresh conversion of the raw file
      (row count, column set, or values), i.e. it is stale or was produced by a
      buggy converter;
    - WARN: the artifact matches a fresh conversion but has derived-column
      consistency findings (``bdf.validate``) that are not documented as
      deliberate in ``deliberate_data_bugs``;
    - PASS: matches fresh conversion and derived-clean (or all findings are
      covered by a documented deliberate bug).
* ``mapping`` — one row per mapped native column: header, BDF column, conversion.
* ``unmapped_native_columns`` — native columns that produce NO BDF column
  ("nothing is lost" check); reviewers must be able to see what is dropped.
* ``stats`` — rows, duration, ranges and null counts for the required columns.
* ``derived_issues`` — findings from ``bdf.validate``'s derived-column checks.
* a small voltage/current-vs-time plot per pair under ``docs/_static/scorecards/``.

Run from the repo root: ``python scripts/build_scorecards.py`` (network required on
first run; subsequent runs hit the fetch cache). ``--check`` recomputes and exits
non-zero if the committed scorecards are stale.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import bdf  # noqa: E402
from bdf.fetch import fetch_url  # noqa: E402
from bdf.plugins import PLUGINS  # noqa: E402
from bdf.spec import COLUMN_ONTOLOGY  # noqa: E402
from bdf.validate import validate_df  # noqa: E402

REF_DIR = REPO_ROOT / "docs" / "examples" / "reference"
MANIFEST_PATH = REF_DIR / "datasets.json"
PLOTS_DIR = REPO_ROOT / "docs" / "_static" / "scorecards"

_SAMPLE_TARGET = 997  # rows sampled for the value comparison
_PLOT_POINTS = 2000


def _label(mr_name: str) -> str:
    q = COLUMN_ONTOLOGY.get(mr_name)
    return q.formatted_label if q is not None else mr_name


def _mapping_and_unmapped(plugin_id: str, raw_headers: list[str]) -> tuple[list[dict], list[str]]:
    """Resolve every raw header through the plugin's normalizer.

    Returns:
        (mapping rows, unmapped native headers). Each mapping row records the
        native header, target BDF column, and the applied conversion.
    """
    normalizer = PLUGINS[plugin_id].table_parser.normalizer
    resolved = normalizer.resolve(raw_headers)
    mapping: list[dict] = []
    mapped_headers: set[str] = set()
    for mr, rc in resolved.items():
        if rc.source_header not in raw_headers:
            continue
        mapped_headers.add(rc.source_header)
        scale = getattr(rc, "scale", None)
        offset = getattr(rc, "offset", None)
        if rc.datetime_fmts:
            conversion = "datetime parse"
        elif (scale in (None, 1.0)) and (offset in (None, 0.0)):
            conversion = "1:1"
        else:
            conversion = f"x {scale:g}" + (f" + {offset:g}" if offset not in (None, 0.0) else "")
        mapping.append({"native": rc.source_header, "bdf": _label(mr), "conversion": conversion})
    unmapped = [h for h in raw_headers if h not in mapped_headers]
    mapping.sort(key=lambda m: m["bdf"])
    return mapping, unmapped


def _compare(fresh: pl.DataFrame, art: pl.DataFrame) -> list[str]:
    """Compare a fresh conversion against the committed artifact. Returns findings."""
    findings: list[str] = []
    if fresh.height != art.height:
        findings.append(f"row count differs: fresh {fresh.height} vs artifact {art.height}")
    col_diff = sorted(set(fresh.columns) ^ set(art.columns))
    if col_diff:
        findings.append(f"column set differs: {col_diff}")
    shared = [c for c in fresh.columns if c in art.columns]
    if fresh.height == art.height and fresh.height:
        idx = np.unique(np.r_[0, np.arange(0, fresh.height, max(1, fresh.height // _SAMPLE_TARGET)), fresh.height - 1])
        bad: list[str] = []
        for c in shared:
            f, a = fresh[c], art[c]
            if f.dtype in (pl.Utf8, pl.String) or a.dtype in (pl.Utf8, pl.String):
                if not (f.gather(idx) == a.gather(idx)).fill_null(True).all():
                    bad.append(c)
                continue
            fv = f.gather(idx).cast(pl.Float64).to_numpy()
            av = a.gather(idx).cast(pl.Float64).to_numpy()
            ok = np.isclose(fv, av, rtol=1e-9, atol=1e-12, equal_nan=True)
            if not ok.all():
                bad.append(f"{c} ({int((~ok).sum())}/{len(ok)} sampled rows)")
        if bad:
            findings.append(f"values differ from a fresh conversion: {bad}")
    return findings


def _stats(art: pl.DataFrame) -> dict:
    stats: dict = {"rows": art.height}
    if "Test Time / s" in art.columns:
        t = art["Test Time / s"]
        stats["duration_hours"] = round(float(t.max() - t.min()) / 3600.0, 2)
    for c in ("Voltage / V", "Current / A"):
        if c in art.columns:
            s = art[c]
            stats[c] = {"min": round(float(s.min()), 4), "max": round(float(s.max()), 4), "nulls": s.null_count()}
    return stats


def _plot(art: pl.DataFrame, out_png: Path, title: str) -> bool:
    """Render a small V/I-vs-time overview plot. Returns False when not plottable."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    needed = {"Test Time / s", "Voltage / V"}
    if not needed.issubset(art.columns):
        return False
    stride = max(1, art.height // _PLOT_POINTS)
    d = art.gather_every(stride)
    t = d["Test Time / s"].cast(pl.Float64).to_numpy() / 3600.0

    fig, ax = plt.subplots(figsize=(7.2, 2.6), dpi=110)
    ax.plot(t, d["Voltage / V"].cast(pl.Float64).to_numpy(), lw=0.9, color="#1f77b4")
    ax.set_xlabel("Test Time / h")
    ax.set_ylabel("Voltage / V", color="#1f77b4")
    ax.tick_params(axis="y", labelcolor="#1f77b4")
    if "Current / A" in d.columns:
        ax2 = ax.twinx()
        ax2.plot(t, d["Current / A"].cast(pl.Float64).to_numpy(), lw=0.7, color="#4d4d4d", alpha=0.8)
        ax2.set_ylabel("Current / A", color="#4d4d4d")
        ax2.tick_params(axis="y", labelcolor="#4d4d4d")
    ax.set_title(title, fontsize=8)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)
    return True


def build_scorecard(entry: dict) -> dict:
    """Compute the scorecard for one manifest entry."""
    name = entry["bdf_file"]
    plugin_id = entry["plugin"]
    raw = entry["raw_files"][0]
    local = fetch_url(raw["url"], filename=raw["filename"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw_df, _ = bdf.read(local, plugin=plugin_id, normalize=False, validate=False, lazy=False)
        fresh, _ = bdf.read(local, plugin=plugin_id, validate=False, lazy=False)
        art, _ = bdf.read(REF_DIR / name, validate=True, lazy=False)
        rep = validate_df(art.to_pandas(), report=False, raise_on_error=False)

    mapping, unmapped = _mapping_and_unmapped(plugin_id, list(raw_df.columns))
    findings = _compare(fresh, art)
    derived = list(rep["derived"]["issues"])

    if findings:
        verdict = "FAIL"
    elif derived and not entry.get("deliberate_data_bugs"):
        verdict = "WARN"
    else:
        verdict = "PASS"

    plot_name = name.rsplit(".bdf.", 1)[0] + ".png"
    has_plot = _plot(art, PLOTS_DIR / plot_name, name)

    return {
        "verdict": verdict,
        "native_columns": len(raw_df.columns),
        "mapped_columns": len(mapping),
        "mapping": mapping,
        "unmapped_native_columns": unmapped,
        "stats": _stats(art),
        "derived_issues": derived,
        "artifact_vs_fresh": findings or ["matches fresh conversion"],
        "plot": plot_name if has_plot else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Recompute and fail if committed scorecards are stale.")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    stale = 0
    for entry in manifest["datasets"]:
        card = build_scorecard(entry)
        old = entry.get("scorecard")
        if args.check:
            old_cmp = {k: v for k, v in (old or {}).items()}
            if old_cmp != card:
                stale += 1
                print(f"[stale] {entry['bdf_file']}")
        else:
            entry["scorecard"] = card
        print(f"[{card['verdict']}] {entry['bdf_file']} (mapped {card['mapped_columns']}/{card['native_columns']})")

    if args.check:
        print(f"check: {stale} stale scorecard(s)")
        return 1 if stale else 0

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
