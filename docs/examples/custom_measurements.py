"""Run the custom-measurement prototype with entirely fabricated data.

From the repository root, with this branch installed in the active environment:

    python docs/examples/custom_measurements.py

The default run writes into a temporary directory and removes it afterward.
To keep the generated CSV, Parquet, and metadata sidecars, supply an output path:

    python docs/examples/custom_measurements.py --output-dir /tmp/bdf-custom-example

This is a sparse illustrative excerpt, not a complete cycler record and not
Microsoft or laboratory data. The invented thickness observations occur at
100% SOC every 100 cycles; intervening rows have no thickness observation.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl
from polars.testing import assert_frame_equal, assert_series_equal

import bdf
from bdf.metadata import Metadata


def example_data() -> tuple[pl.DataFrame, Metadata]:
    """Create a synthetic 21-row excerpt with ten sparse thickness values."""
    cycles = list(range(0, 1001, 50))
    frame = pl.DataFrame(
        {
            "Test Time / s": [float(cycle * 3600) for cycle in cycles],
            "Voltage / V": [4.2 if cycle % 100 == 0 else 3.7 for cycle in cycles],
            "Current / A": [0.0] * len(cycles),
            "Cycle Count / 1": cycles,
            "Thickness / mm": [
                round(5.0 + cycle * 0.0001, 2) if cycle > 0 and cycle % 100 == 0 else None for cycle in cycles
            ],
        }
    )
    metadata = Metadata.model_validate(
        {
            "battinfo_dataset": {
                "dataset": {
                    "name": "SYNTHETIC sparse pouch-thickness example",
                    "description": (
                        "Entirely fabricated values for a BDF prototype, not Microsoft or laboratory data. "
                        "An illustrative 21-row excerpt from an invented 1,000-cycle test; "
                        "the rows and their times are fabricated, not a complete cycler record."
                    ),
                    "variable_measured": [
                        {
                            "name": "Thickness",
                            "unit_text": "mm",
                            # Reuse the existing EMMO quantity even though BDF has
                            # no core thickness column. Intermittency changes the
                            # observation schedule, not the physical quantity.
                            "same_as": "https://w3id.org/emmo#EMMO_43003c86_9d15_433b_9789_ee2940920656",
                            "description": (
                                "Fabricated pouch thickness representing parallel plate height-gauge "
                                "measurements at 100% SOC every 100 cycles, from cycle 100 to 1000. "
                                "Each value belongs to its row's test time and cycle. "
                                "Null means no observation on that row; do not fill or interpolate."
                            ),
                        }
                    ],
                }
            }
        }
    )
    return frame, metadata


def demonstrate(output_dir: Path) -> None:
    """Verify that standard data, sparse values, and declarations round-trip."""
    original, metadata = example_data()
    csv_path = output_dir / "sparse_thickness.bdf.csv"
    parquet_path = output_dir / "sparse_thickness_typed.bdf.parquet"

    # Parquet retains the input's numeric types as well as its values.
    bdf.save(original, parquet_path, metadata=metadata)

    # The sidecar declaration retains Thickness / mm without include_unknown=True.
    eager, restored_metadata = bdf.read(parquet_path)
    lazy, scanned_metadata = bdf.scan(parquet_path)
    assert_frame_equal(original, eager.select(original.columns))
    assert_frame_equal(original, lazy.select(original.columns).collect())
    assert restored_metadata.battinfo_dataset == metadata.battinfo_dataset
    assert scanned_metadata.battinfo_dataset == metadata.battinfo_dataset

    # Reuse the returned metadata when saving so the declaration follows the data.
    bdf.save(eager, csv_path, metadata=restored_metadata)
    copied, copied_metadata = bdf.read(csv_path)
    core_columns = [column for column in original.columns if column != "Thickness / mm"]
    assert_frame_equal(original.select(core_columns), copied.select(core_columns))

    # CSV's current parser preserves custom cells as text; the declaration does
    # not request a numeric cast. Check the literal values and nulls separately.
    assert_series_equal(original["Thickness / mm"].cast(pl.String), copied["Thickness / mm"].cast(pl.String))
    assert copied_metadata.battinfo_dataset == metadata.battinfo_dataset

    measured = eager.filter(pl.col("Thickness / mm").is_not_null())
    print("SYNTHETIC DATA: ten invented thickness observations, eleven null rows.")
    print(measured.select("Cycle Count / 1", "Thickness / mm"))
    print("Verified: standard values, sparse thickness values, nulls, and metadata are unchanged.")
    print("Parquet retains numeric types; custom CSV cells are retained as text by the current parser.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Keep the generated example files in this directory.")
    args = parser.parse_args()
    if args.output_dir is not None:
        demonstrate(args.output_dir)
        print(f"Example files: {args.output_dir.resolve()}")
    else:
        with TemporaryDirectory(prefix="bdf-custom-measurements-") as temporary:
            demonstrate(Path(temporary))
        print("Temporary example files have been removed.")


if __name__ == "__main__":
    main()
