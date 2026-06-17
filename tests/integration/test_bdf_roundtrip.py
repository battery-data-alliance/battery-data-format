"""Generated multi-format round-trip and detection coverage for BDF artifacts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from bdf.plugins import PLUGINS, detect
from bdf.spec import COLUMN_ONTOLOGY

# --------------------------------------------------------------------------- #
# Semantic families (mirror the groupings in test_column_validity.py)
# --------------------------------------------------------------------------- #

_CATEGORICAL = "CATEGORICAL"
_WEAK_ID = "WEAK_ID"
_COUNTER = "COUNTER"
_COUNTER_UNIQUE = "COUNTER_UNIQUE"
_STRICT_INC = "STRICT_INC"
_STEP_INDEX = "STEP_INDEX"
_MONO_POS = "MONO_POS"
_SIGNED = "SIGNED"
_FINITE = "FINITE"
_NONNEG = "NONNEG"
_TEMP = "TEMP"
_STEP_RESET_MONO = "STEP_RESET_MONO"
_STEP_RESET_SIGNED = "STEP_RESET_SIGNED"
_CYCLE_RESET_MONO = "CYCLE_RESET_MONO"
_CYCLE_RESET_SIGNED = "CYCLE_RESET_SIGNED"

_INT_FAMILY: dict[str, str] = {
    "record_index": _STRICT_INC,
    "step_count": _COUNTER_UNIQUE,
    "step_index": _STEP_INDEX,
    "step_id": _WEAK_ID,
    "cycle_count": _COUNTER,
}

# Signed instantaneous / impedance quantities that may legitimately be negative.
_SIGNED_FLOAT: frozenset[str] = frozenset(
    {"current_ampere", "power_watt", "real_impedance_ohm", "imaginary_impedance_ohm", "phase_degree"}
)


def _family(mr: str, dtype: str) -> str:
    """Classify a quantity into a generator family, raising on an unknown shape.

    Derivation is structural (dtype, then name tokens) so a newly added column is
    classified automatically; anything that falls through raises, forcing an explicit
    decision rather than emitting an unconstrained column.

    Args:
        mr: Machine-readable quantity name.
        dtype: The quantity's spec dtype (``"int"``, ``"float"``, or ``"str"``).

    Returns:
        A family constant.

    Raises:
        ValueError: When no family rule matches the quantity.
    """
    if dtype == "str":
        return _CATEGORICAL
    if dtype == "int":
        if mr in _INT_FAMILY:
            return _INT_FAMILY[mr]
        raise ValueError(f"no generator family for integer quantity {mr!r}")
    if "time" in mr:
        return _STEP_RESET_MONO if mr.startswith("step_") else _MONO_POS
    if mr.startswith("step_net"):
        return _STEP_RESET_SIGNED
    if mr.startswith("cycle_net"):
        return _CYCLE_RESET_SIGNED
    if mr.startswith("step_"):
        return _STEP_RESET_MONO
    if mr.startswith("cycle_"):
        return _CYCLE_RESET_MONO
    if mr in ("net_capacity_ah", "net_energy_wh"):
        return _SIGNED
    if "capacity" in mr or "energy" in mr:
        return _MONO_POS
    if "temperature" in mr:
        return _TEMP
    if "pressure" in mr:
        return _NONNEG
    if mr in _SIGNED_FLOAT:
        return _FINITE
    if "resistance" in mr or mr == "absolute_impedance_ohm" or mr == "frequency_hertz":
        return _NONNEG
    if mr == "voltage_volt":
        return _FINITE
    raise ValueError(f"no generator family for quantity {mr!r}")


# Frame geometry: enough steps and cycles that resetting families show ≥ 2 groups.
_N_CYCLES = 2
_STEPS_PER_CYCLE = 3
_ROWS_PER_STEP = 5
_N_STEPS = _N_CYCLES * _STEPS_PER_CYCLE
_N_ROWS = _N_STEPS * _ROWS_PER_STEP


def _row_layout() -> dict[str, list[int]]:
    """Return per-row step/cycle bookkeeping arrays of length ``_N_ROWS``."""
    cycle_of_row: list[int] = []
    step_exec_of_row: list[int] = []
    within_step_of_row: list[int] = []
    within_cycle_of_row: list[int] = []
    step_id_of_row: list[int] = []
    for s in range(_N_STEPS):
        cycle = s // _STEPS_PER_CYCLE
        step_in_cycle = s % _STEPS_PER_CYCLE
        for r in range(_ROWS_PER_STEP):
            cycle_of_row.append(cycle)
            step_exec_of_row.append(s)
            within_step_of_row.append(r)
            within_cycle_of_row.append(step_in_cycle * _ROWS_PER_STEP + r)
            step_id_of_row.append(step_in_cycle)
    return {
        "cycle": cycle_of_row,
        "step_exec": step_exec_of_row,
        "within_step": within_step_of_row,
        "within_cycle": within_cycle_of_row,
        "step_id": step_id_of_row,
    }


def _generate_column(family: str, layout: dict[str, list[int]]) -> pl.Series:
    """Build one valid-by-construction column for ``family`` over the row layout."""
    n = _N_ROWS
    ws = layout["within_step"]
    wc = layout["within_cycle"]
    cyc = layout["cycle"]
    step_exec = layout["step_exec"]
    sid = layout["step_id"]

    if family == _CATEGORICAL:
        names = ["charge", "discharge", "rest"]
        return pl.Series([names[s % len(names)] for s in step_exec], dtype=pl.Utf8)
    if family == _STRICT_INC:
        return pl.Series(list(range(n)), dtype=pl.Int64)
    if family == _COUNTER:
        return pl.Series(cyc, dtype=pl.Int64)
    if family == _COUNTER_UNIQUE:
        return pl.Series(step_exec, dtype=pl.Int64)
    if family == _STEP_INDEX:
        return pl.Series([r + 1 for r in ws], dtype=pl.Int64)
    if family == _WEAK_ID:
        return pl.Series(sid, dtype=pl.Int64)
    if family == _MONO_POS:
        return pl.Series([i * 10.0 for i in range(n)], dtype=pl.Float64)
    if family == _SIGNED:
        return pl.Series([(i - n / 2) * 0.1 for i in range(n)], dtype=pl.Float64)
    if family == _FINITE:
        return pl.Series([3.6 + (i % 7) * 0.05 - 0.15 * (i % 2) for i in range(n)], dtype=pl.Float64)
    if family == _NONNEG:
        return pl.Series([0.01 + (i % 5) * 0.2 for i in range(n)], dtype=pl.Float64)
    if family == _TEMP:
        return pl.Series([25.0 + (i % 6) for i in range(n)], dtype=pl.Float64)
    if family == _STEP_RESET_MONO:
        return pl.Series([r * 10.0 for r in ws], dtype=pl.Float64)
    if family == _STEP_RESET_SIGNED:
        return pl.Series(
            [r * 0.1 * (1 if e % 2 == 0 else -1) for r, e in zip(ws, step_exec, strict=True)], dtype=pl.Float64
        )
    if family == _CYCLE_RESET_MONO:
        return pl.Series([r * 1.0 for r in wc], dtype=pl.Float64)
    if family == _CYCLE_RESET_SIGNED:
        return pl.Series([r * 0.1 * (1 if c % 2 == 0 else -1) for r, c in zip(wc, cyc, strict=True)], dtype=pl.Float64)
    raise ValueError(f"no value generator for family {family!r}")  # pragma: no cover


def canonical_bdf_frame() -> pl.DataFrame:
    """Build the canonical BDF frame: one column per non-deprecated ontology quantity.

    Each column is headed by the quantity's ``formatted_label`` and populated so its
    values satisfy the quantity's semantic family by construction. Deprecated
    quantities are excluded — the BDF normalizer drops them on read, so they cannot
    round-trip.

    Returns:
        A polars DataFrame with canonical-label columns over ``_N_ROWS`` rows.
    """
    layout = _row_layout()
    columns: dict[str, pl.Series] = {}
    for mr, q in COLUMN_ONTOLOGY:
        if q.deprecated:
            continue
        family = _family(mr, q.dtype)
        columns[q.formatted_label] = _generate_column(family, layout)
    return pl.DataFrame(columns)


@pytest.fixture(scope="module")
def canonical_frame() -> pl.DataFrame:
    """The single canonical BDF frame shared across the round-trip matrix."""
    return canonical_bdf_frame()


@pytest.fixture(scope="module")
def variant_paths(canonical_frame: pl.DataFrame, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Write the canonical frame to the four format variants in a temp directory.

    Args:
        canonical_frame: The generated canonical BDF frame.
        tmp_path_factory: Pytest factory for a module-scoped temp directory.

    Returns:
        Mapping of variant id to written file path.
    """
    d = tmp_path_factory.mktemp("bdf_roundtrip")
    paths = {
        "bdf_csv": d / "sample.bdf.csv",
        "csv": d / "sample.csv",
        "bdf_parquet": d / "sample.bdf.parquet",
        "parquet": d / "sample.parquet",
    }
    canonical_frame.write_csv(paths["bdf_csv"])
    canonical_frame.write_csv(paths["csv"])
    canonical_frame.write_parquet(paths["bdf_parquet"])
    canonical_frame.write_parquet(paths["parquet"])
    return paths


# (variant id, expected plugin id, deciding stage)
_MATRIX: list[tuple[str, str, str]] = [
    ("bdf_csv", "bdf_csv", "ext"),
    ("csv", "bdf_csv", "columns"),
    ("bdf_parquet", "bdf_parquet", "ext"),
    ("parquet", "bdf_parquet", "ext"),
]


def test_canonical_frame_covers_every_nondeprecated_column(canonical_frame: pl.DataFrame) -> None:
    """The frame holds exactly one column per non-deprecated quantity, by formatted label."""
    expected = {q.formatted_label for _, q in COLUMN_ONTOLOGY if not q.deprecated}
    assert set(canonical_frame.columns) == expected
    assert canonical_frame.height == _N_ROWS


@pytest.mark.parametrize("variant,expected_id,stage", _MATRIX, ids=[m[0] for m in _MATRIX])
def test_variant_detection(variant: str, expected_id: str, stage: str, variant_paths: dict[str, Path]) -> None:
    """Each variant resolves to the expected BDF plugin and stops at the expected stage."""
    import bdf.plugins as _mod

    path = variant_paths[variant]
    with (
        patch.object(_mod, "detect_from_metadata", wraps=_mod.detect_from_metadata) as spy_meta,
        patch.object(_mod, "detect_from_columns", wraps=_mod.detect_from_columns) as spy_cols,
    ):
        plugin_id, plugin = detect(path)

    assert plugin_id == expected_id
    assert plugin is PLUGINS[expected_id]
    if stage == "ext":
        assert not spy_cols.called, "column stage ran — expected ext/magic to be decisive"
    elif stage == "columns":
        assert spy_meta.called and spy_cols.called, "expected metadata and column stages to run"


@pytest.mark.parametrize("variant,expected_id,stage", _MATRIX, ids=[m[0] for m in _MATRIX])
def test_variant_roundtrip_equals_canonical(
    variant: str, expected_id: str, stage: str, variant_paths: dict[str, Path], canonical_frame: pl.DataFrame
) -> None:
    """Reading a variant reproduces the canonical frame: same labels/dtypes, equal values."""
    actual = PLUGINS[expected_id].table_parser.read(variant_paths[variant]).collect()

    assert set(actual.columns) == set(canonical_frame.columns)
    for col in canonical_frame.columns:
        exp = canonical_frame[col]
        got = actual[col]
        if exp.dtype == pl.Utf8:
            assert got.dtype == pl.Utf8, f"{col}: expected string dtype, got {got.dtype}"
            assert got.to_list() == exp.to_list(), f"{col}: string values differ"
        elif exp.dtype.is_integer():
            assert got.dtype.is_integer(), f"{col}: expected integer dtype, got {got.dtype}"
            assert got.to_list() == exp.to_list(), f"{col}: integer values differ"
        else:
            assert got.dtype.is_float(), f"{col}: expected float dtype, got {got.dtype}"
            assert got.to_list() == pytest.approx(exp.to_list(), rel=1e-9, abs=1e-9), f"{col}: float values differ"
