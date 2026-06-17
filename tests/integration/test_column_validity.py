"""Per-column structural validation of BDF columns produced from the test cases.

One test per BDF quantity, parametrized over the test cases that produce it
(skipped when a case does not). Each test applies the quantity's semantic-family
assertion, which encodes the ontology definition (reset behaviour, monotonicity,
sign, range). Presence/dtype/null are covered by ``test_table_parsers.py``.

Each BDF quantity belongs to a *semantic family* whose assertion encodes the
ontology definition: global-monotonic cumulative (MONO+), signed net (SIGNED),
step-resetting magnitude/signed/index (STEP_RESET_*), counters, weak identifier,
instantaneous finite, non-negative finite, temperature range, categorical.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from bdf.plugins import PLUGINS
from bdf.spec import COLUMN_ONTOLOGY
from integration.test_cases import ALL_CASES, SampleCase, get_sample_data_source

# Plausible cell/pack temperature window in degree Celsius. Out-of-range
# sentinel values (e.g. -9999) intentionally fail the TEMP check.
_TEMP_MIN_C = -50.0
_TEMP_MAX_C = 150.0

# Generated BDF data is valid by construction and round-trip-checked by exact
# equality in ``test_bdf_roundtrip.py``; the physics invariants here apply to
# vendor exports only, so BDF cases are excluded from this corpus path.
_EXCLUDED_PLUGIN_IDS: frozenset[str] = frozenset({"bdf_csv", "bdf_parquet"})


def label_of(mr: str) -> str:
    """Return the canonical BDF label for a machine-readable quantity name.

    Args:
        mr: Machine-readable quantity name (e.g. ``"voltage_volt"``).

    Returns:
        Formatted BDF label (e.g. ``"Voltage / V"``).
    """
    return COLUMN_ONTOLOGY[mr].formatted_label


_DF_CACHE: dict[str, pl.DataFrame] = {}


def load_df(case: SampleCase, data_dir: Path) -> pl.DataFrame:
    """Read a case to a collected DataFrame, once per source.

    The collected frame is cached by source so the per-column tests that share a
    case (and its network fetch) pay a single read.

    Args:
        case: The case to read.
        data_dir: Directory holding local sample data.

    Returns:
        The parser's canonical-unit output collected to a polars DataFrame.
    """
    key = case.source
    if key not in _DF_CACHE:
        path = get_sample_data_source(case.source, case.is_url, data_dir)
        _DF_CACHE[key] = PLUGINS[case.plugin_id].table_parser.read(path).collect()
    return _DF_CACHE[key]


def cases_for(mr: str) -> list:
    """Parametrize params for every case whose ``expected_columns`` produce ``mr``.

    Args:
        mr: Machine-readable quantity name.

    Returns:
        List of ``pytest.param`` entries; a single skip param when no case
        produces the quantity.
    """
    params = [
        pytest.param(cid, c, marks=c.marks, id=cid)
        for cid, c in ALL_CASES
        if mr in (c.expected_columns or {}) and c.plugin_id not in _EXCLUDED_PLUGIN_IDS
    ]
    if not params:
        skip = pytest.mark.skip(reason=f"no case produces {mr}")
        return [pytest.param(None, None, marks=skip, id="none")]
    return params


def step_count(df: pl.DataFrame) -> pl.Series | None:
    """Derive the per-row step count (a unique id per step execution), or None.

    This reconstructs the ``step_count`` quantity used to group step-reset
    checks. Prefers the actual ``step_count`` column (unique per execution);
    otherwise run-length encodes over ``(cycle_count, step_id)`` so a ``step_id``
    recurring in the next cycle starts a new step; otherwise uses ``step_index``
    resets.

    Args:
        df: Canonical-label DataFrame.

    Returns:
        Integer step count per row, or None if no source column is present.
    """
    cols = df.columns
    if (sc := label_of("step_count")) in cols:
        return df[sc]

    changed: pl.Expr | None = None
    for mr in ("cycle_count", "step_id"):
        if (lbl := label_of(mr)) in cols:
            delta = pl.col(lbl) != pl.col(lbl).shift(1)
            changed = delta if changed is None else (changed | delta)
    if changed is None and (si := label_of("step_index")) in cols:
        changed = pl.col(si) <= pl.col(si).shift(1)
    if changed is None:
        return None
    return df.select(changed.fill_null(True).cum_sum().alias("step_count"))["step_count"]


def cycle_count(df: pl.DataFrame) -> pl.Series | None:
    """Derive the per-row cycle count from ``cycle_count``, or None.

    Cycle quantities reset when ``cycle_count`` increments, so the count is the
    run-length encoding of consecutive equal ``cycle_count`` values.

    Args:
        df: Canonical-label DataFrame.

    Returns:
        Integer cycle count per row, or None if ``cycle_count`` is absent.
    """
    if (cc := label_of("cycle_count")) in df.columns:
        changed = pl.col(cc) != pl.col(cc).shift(1)
        return df.select(changed.fill_null(True).cum_sum().alias("cycle_count"))["cycle_count"]
    return None


def _atol(s: pl.Series) -> float:
    """Tolerance scaled to the column magnitude, with a small floor.

    Relative factor of 1e-5 absorbs last-digit rounding noise in source files
    that only carry ~6 significant figures of decimal text (e.g. Maccor).
    """
    m = s.abs().max()
    return max(1e-9, 1e-5 * float(m)) if m is not None else 1e-9


def _all(expr: pl.Expr, df: pl.DataFrame) -> bool:
    """Evaluate ``expr.all()`` (nulls ignored) over ``df`` to a Python bool."""
    return bool(df.select(expr.all()).item())


# --------- Family assertions ----------


def assert_finite(df: pl.DataFrame, label: str) -> None:
    """FINITE: every value is finite (no NaN/inf)."""
    s = df[label].drop_nulls()
    assert s.len(), f"{label}: no values"
    assert s.is_finite().all(), f"{label}: non-finite values present"


def assert_nonneg_finite(df: pl.DataFrame, label: str) -> None:
    """NONNEG_FINITE: non-null values are finite and >= 0."""
    s = df[label].drop_nulls()
    assert s.is_finite().all(), f"{label}: non-finite values present"
    assert s.min() >= -_atol(s), f"{label}: negative values present"


def assert_mono_nonneg(df: pl.DataFrame, label: str) -> None:
    """MONO+: finite, >= 0, globally non-decreasing."""
    s = df[label].drop_nulls()
    assert s.len(), f"{label}: no values"
    assert s.is_finite().all(), f"{label}: non-finite values present"
    tol = _atol(s)
    assert s.min() >= -tol, f"{label}: negative values present"
    assert _all(pl.col(label).diff() >= -tol, df), f"{label}: not globally non-decreasing"


def assert_signed(df: pl.DataFrame, label: str) -> None:
    """SIGNED: finite only (may be negative, not monotonic)."""
    assert_finite(df, label)


def _grouped(df: pl.DataFrame, label: str, group_fn, group_name: str) -> tuple[pl.DataFrame, bool] | None:
    """Return a (value, group) frame plus whether >= 2 groups exist, or None."""
    g = group_fn(df)
    if g is None:
        return None
    f = pl.DataFrame({"value": df[label], group_name: g})
    return f, g.n_unique() >= 2


def _assert_reset_mono(df: pl.DataFrame, label: str, group_fn, group_name: str, scope: str) -> None:
    """Reset-magnitude check: >=0, non-decreasing within group, resets per group."""
    s = df[label]
    assert s.is_finite().all(), f"{label}: non-finite values present"
    tol = _atol(s)
    assert s.min() >= -tol, f"{label}: negative values present"

    grouped = _grouped(df, label, group_fn, group_name)
    if grouped is None:
        pytest.skip(f"{label}: no {scope}-group key to verify reset")
    assert grouped is not None  # narrow: pytest.skip above is terminal
    f, multi = grouped
    assert _all(pl.col("value").diff().over(group_name) >= -tol, f), f"{label}: decreases within a {scope}"
    if not multi:
        pytest.skip(f"{label}: fewer than two {scope}-groups")

    reset_tol = tol + 0.05 * float(s.abs().max() or 0.0)
    is_start = pl.col(group_name) != pl.col(group_name).shift(1)
    prev = pl.col("value").shift(1)
    boundary_ok = (
        pl.when(is_start & prev.is_not_null())
        .then((pl.col("value") <= prev + tol) | (pl.col("value").abs() <= reset_tol))
        .otherwise(True)
    )
    assert _all(boundary_ok, f), f"{label}: did not reset at a {scope} boundary"


def _assert_reset_signed(df: pl.DataFrame, label: str, group_fn, group_name: str, scope: str) -> None:
    """Reset-signed check: finite, each group starts near zero."""
    s = df[label]
    assert s.is_finite().all(), f"{label}: non-finite values present"
    grouped = _grouped(df, label, group_fn, group_name)
    if grouped is None:
        pytest.skip(f"{label}: no {scope}-group key to verify reset")
    assert grouped is not None  # narrow: pytest.skip above is terminal
    f, multi = grouped
    if not multi:
        pytest.skip(f"{label}: fewer than two {scope}-groups")

    reset_tol = _atol(s) + 0.05 * float(s.abs().max() or 0.0)
    is_start = (pl.col(group_name) != pl.col(group_name).shift(1)).fill_null(True)
    starts = f.filter(is_start)["value"]
    assert (starts.abs() <= reset_tol).all(), f"{label}: a {scope}-group start is not near zero"


def assert_step_reset_mono(df: pl.DataFrame, label: str) -> None:
    """STEP_RESET_MONO: >=0, non-decreasing within step, resets at transition."""
    _assert_reset_mono(df, label, step_count, "step_count", "step")


def assert_cycle_reset_mono(df: pl.DataFrame, label: str) -> None:
    """CYCLE_RESET_MONO: >=0, non-decreasing within cycle, resets at cycle_count increment."""
    _assert_reset_mono(df, label, cycle_count, "cycle_count", "cycle")


def assert_step_reset_signed(df: pl.DataFrame, label: str) -> None:
    """STEP_RESET_SIGNED: finite, each step-group starts near zero."""
    _assert_reset_signed(df, label, step_count, "step_count", "step")


def assert_cycle_reset_signed(df: pl.DataFrame, label: str) -> None:
    """CYCLE_RESET_SIGNED: finite, each cycle-group starts near zero."""
    _assert_reset_signed(df, label, cycle_count, "cycle_count", "cycle")


def assert_step_reset_self(df: pl.DataFrame, label: str) -> None:
    """STEP_RESET_MONO validated from the column's own resets.

    For BioLogic GITT exports the ``step time/s`` reset is not aligned to the
    ``Ns`` (step_id) transitions: the timer zeroes one row *after* each ``Ns``
    change and the same ``Ns`` recurs across loops. So instead of grouping by
    step_id, the reset structure is read directly from the column: non-negative,
    drops to ~zero at least once, and every decrease is such a reset (no partial
    decrease within a step).
    """
    s = df[label]
    assert s.is_finite().all(), f"{label}: non-finite values present"
    tol = _atol(s)
    assert s.min() >= -tol, f"{label}: negative values present"

    reset_tol = tol + 1e-3 * float(s.abs().max() or 0.0)
    drops = df.select(value=s).with_columns(delta=pl.col("value").diff()).filter(pl.col("delta") < -tol)
    assert drops.height, f"{label}: never resets"
    assert (drops["value"].abs() <= reset_tol).all(), f"{label}: a decrease is not a reset to zero"


def assert_step_reset_index(df: pl.DataFrame, label: str) -> None:
    """STEP_RESET_INDEX: ==1 at each step start, +1 within step."""
    grouped = _grouped(df, label, step_count, "step_count")
    if grouped is None:
        pytest.skip(f"{label}: no step-group key to verify reset")
    assert grouped is not None  # narrow: pytest.skip above is terminal
    f, multi = grouped
    if not multi:
        pytest.skip(f"{label}: fewer than two step-groups")

    is_start = (pl.col("step_count") != pl.col("step_count").shift(1)).fill_null(True)
    assert _all(pl.when(is_start).then(pl.col("value") == 1).otherwise(True), f), (
        f"{label}: does not reset to 1 at each step"
    )
    within = pl.when(is_start).then(None).otherwise(pl.col("value").diff().over("step_count") == 1)
    assert _all(within, f), f"{label}: does not increment by 1 within step"


def assert_counter_nonneg(df: pl.DataFrame, label: str) -> None:
    """COUNTER+: integer >= 0, globally non-decreasing."""
    s = df[label].drop_nulls()
    assert s.len(), f"{label}: no values"
    assert s.min() >= 0, f"{label}: negative counter value"
    assert _all(pl.col(label).diff() >= 0, df), f"{label}: counter decreases"


def assert_counter_unique(df: pl.DataFrame, label: str) -> None:
    """COUNTER_UNIQUE: non-decreasing integer that never revisits a value.

    A non-decreasing sequence can never revisit a value once it increases, so
    non-decreasing + a positive jump at every change guarantees each step
    execution gets a unique value.
    """
    d = pl.col(label).diff()
    assert _all(d >= 0, df), f"{label}: counter decreases"
    assert _all(pl.when(d != 0).then(d > 0).otherwise(True), df), f"{label}: non-positive jump at a step change"


def assert_strict_increasing(df: pl.DataFrame, label: str) -> None:
    """STRICT_INC: strictly increasing integer."""
    assert df[label].len(), f"{label}: no values"
    assert _all(pl.col(label).diff() > 0, df), f"{label}: not strictly increasing"


def assert_weak_id(df: pl.DataFrame, label: str) -> None:
    """WEAK_ID: integer-typed with values; no ordering constraint (placeholder)."""
    assert df.schema[label].is_integer(), f"{label}: expected integer dtype"
    assert df[label].drop_nulls().len(), f"{label}: no values"


def assert_temp_range(df: pl.DataFrame, label: str) -> None:
    """TEMP: finite and within a plausible physical range (sentinels fail)."""
    s = df[label].drop_nulls()
    assert s.len(), f"{label}: no values"
    assert s.is_finite().all(), f"{label}: non-finite values present"
    lo, hi = s.min(), s.max()
    assert lo >= _TEMP_MIN_C, f"{label}: value {lo} below {_TEMP_MIN_C}C (sentinel?)"
    assert hi <= _TEMP_MAX_C, f"{label}: value {hi} above {_TEMP_MAX_C}C (sentinel?)"


def assert_categorical(df: pl.DataFrame, label: str) -> None:
    """CATEGORICAL: every value is a non-empty string."""
    s = df[label].drop_nulls()
    assert s.len(), f"{label}: no values"
    assert s.dtype == pl.Utf8, f"{label}: expected string dtype, got {s.dtype}"
    assert (s.str.len_chars() > 0).all(), f"{label}: empty string values present"


def run_check(case: SampleCase | None, data_dir: Path, mr: str, assertion) -> None:
    """Load the case's column and run a family assertion, skipping when absent.

    Args:
        case: The test case (None only for an already-skipped param).
        data_dir: Directory holding local sample data.
        mr: Machine-readable quantity name to validate.
        assertion: Family assertion to apply to ``(df, label)``.
    """
    assert case is not None  # the None param carries a skip mark
    df = load_df(case, data_dir)
    label = label_of(mr)
    if label not in df.columns:
        pytest.skip(f"{mr} not produced by {case.source}")
    if mr in case.known_validity_bugs:
        pytest.xfail(case.known_validity_bugs[mr])
    assertion(df, label)


class TestMonoNonneg:
    """MONO+: finite, >=0, globally non-decreasing (never resets)."""

    @pytest.mark.parametrize("cid,case", cases_for("test_time_second"))
    def test_test_time_second(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "test_time_second", assert_mono_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("unix_time_second"))
    def test_unix_time_second(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "unix_time_second", assert_mono_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("cumulative_capacity_ah"))
    def test_cumulative_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cumulative_capacity_ah", assert_mono_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("cumulative_energy_wh"))
    def test_cumulative_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cumulative_energy_wh", assert_mono_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("charging_capacity_ah"))
    def test_charging_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "charging_capacity_ah", assert_mono_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("discharging_capacity_ah"))
    def test_discharging_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "discharging_capacity_ah", assert_mono_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("charging_energy_wh"))
    def test_charging_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "charging_energy_wh", assert_mono_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("discharging_energy_wh"))
    def test_discharging_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "discharging_energy_wh", assert_mono_nonneg)


class TestSigned:
    """SIGNED: finite, may be negative, not monotonic."""

    @pytest.mark.parametrize("cid,case", cases_for("net_capacity_ah"))
    def test_net_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "net_capacity_ah", assert_signed)

    @pytest.mark.parametrize("cid,case", cases_for("net_energy_wh"))
    def test_net_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "net_energy_wh", assert_signed)


class TestStepResetMono:
    """STEP_RESET_MONO: >=0, non-decreasing within step, resets."""

    @pytest.mark.parametrize("cid,case", cases_for("step_time_second"))
    def test_step_time_second(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        # BioLogic 'step time/s' is not aligned to Ns transitions (timer zeroes one
        # row after each Ns change; the same Ns recurs across GITT loops), so its
        # reset structure is validated from the column itself, not step_id groups.
        biologic = case is not None and case.plugin_id == "biologic_mpt"
        assertion = assert_step_reset_self if biologic else assert_step_reset_mono
        run_check(case, data_dir, "step_time_second", assertion)

    @pytest.mark.parametrize("cid,case", cases_for("step_cumulative_capacity_ah"))
    def test_step_cumulative_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_cumulative_capacity_ah", assert_step_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("step_cumulative_energy_wh"))
    def test_step_cumulative_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_cumulative_energy_wh", assert_step_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("step_charging_capacity_ah"))
    def test_step_charging_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_charging_capacity_ah", assert_step_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("step_discharging_capacity_ah"))
    def test_step_discharging_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_discharging_capacity_ah", assert_step_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("step_charging_energy_wh"))
    def test_step_charging_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_charging_energy_wh", assert_step_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("step_discharging_energy_wh"))
    def test_step_discharging_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_discharging_energy_wh", assert_step_reset_mono)


class TestStepResetSignedAndIndex:
    """STEP_RESET_SIGNED / STEP_RESET_INDEX."""

    @pytest.mark.parametrize("cid,case", cases_for("step_net_capacity_ah"))
    def test_step_net_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_net_capacity_ah", assert_step_reset_signed)

    @pytest.mark.parametrize("cid,case", cases_for("step_net_energy_wh"))
    def test_step_net_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_net_energy_wh", assert_step_reset_signed)

    @pytest.mark.parametrize("cid,case", cases_for("step_index"))
    def test_step_index(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_index", assert_step_reset_index)


class TestCycleResetMono:
    """CYCLE_RESET_MONO: >=0, non-decreasing within cycle, resets."""

    @pytest.mark.parametrize("cid,case", cases_for("cycle_cumulative_capacity_ah"))
    def test_cycle_cumulative_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_cumulative_capacity_ah", assert_cycle_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("cycle_cumulative_energy_wh"))
    def test_cycle_cumulative_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_cumulative_energy_wh", assert_cycle_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("cycle_charging_capacity_ah"))
    def test_cycle_charging_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_charging_capacity_ah", assert_cycle_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("cycle_discharging_capacity_ah"))
    def test_cycle_discharging_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_discharging_capacity_ah", assert_cycle_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("cycle_charging_energy_wh"))
    def test_cycle_charging_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_charging_energy_wh", assert_cycle_reset_mono)

    @pytest.mark.parametrize("cid,case", cases_for("cycle_discharging_energy_wh"))
    def test_cycle_discharging_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_discharging_energy_wh", assert_cycle_reset_mono)


class TestCycleResetSigned:
    """CYCLE_RESET_SIGNED: signed, resets to zero per cycle."""

    @pytest.mark.parametrize("cid,case", cases_for("cycle_net_capacity_ah"))
    def test_cycle_net_capacity_ah(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_net_capacity_ah", assert_cycle_reset_signed)

    @pytest.mark.parametrize("cid,case", cases_for("cycle_net_energy_wh"))
    def test_cycle_net_energy_wh(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_net_energy_wh", assert_cycle_reset_signed)


class TestCountersIdentifier:
    """Counters / identifier."""

    @pytest.mark.parametrize("cid,case", cases_for("cycle_count"))
    def test_cycle_count(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "cycle_count", assert_counter_nonneg)

    @pytest.mark.parametrize("cid,case", cases_for("step_count"))
    def test_step_count(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_count", assert_counter_unique)

    @pytest.mark.parametrize("cid,case", cases_for("record_index"))
    def test_record_index(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "record_index", assert_strict_increasing)

    @pytest.mark.parametrize("cid,case", cases_for("step_id"))
    def test_step_id(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_id", assert_weak_id)


class TestInstantaneousRangeCategorical:
    """Instantaneous / range / categorical."""

    @pytest.mark.parametrize("cid,case", cases_for("voltage_volt"))
    def test_voltage_volt(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "voltage_volt", assert_finite)

    @pytest.mark.parametrize("cid,case", cases_for("current_ampere"))
    def test_current_ampere(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "current_ampere", assert_finite)

    @pytest.mark.parametrize("cid,case", cases_for("power_watt"))
    def test_power_watt(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "power_watt", assert_finite)

    @pytest.mark.parametrize("cid,case", cases_for("internal_resistance_ohm"))
    def test_internal_resistance_ohm(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "internal_resistance_ohm", assert_nonneg_finite)

    @pytest.mark.parametrize("cid,case", cases_for("ac_internal_resistance_ohm"))
    def test_ac_internal_resistance_ohm(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "ac_internal_resistance_ohm", assert_nonneg_finite)

    @pytest.mark.parametrize("cid,case", cases_for("dc_internal_resistance_ohm"))
    def test_dc_internal_resistance_ohm(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "dc_internal_resistance_ohm", assert_nonneg_finite)

    @pytest.mark.parametrize("cid,case", cases_for("temperature_t1_celsius"))
    def test_temperature_t1_celsius(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "temperature_t1_celsius", assert_temp_range)

    @pytest.mark.parametrize("cid,case", cases_for("temperature_t2_celsius"))
    def test_temperature_t2_celsius(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "temperature_t2_celsius", assert_temp_range)

    @pytest.mark.parametrize("cid,case", cases_for("temperature_t3_celsius"))
    def test_temperature_t3_celsius(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "temperature_t3_celsius", assert_temp_range)

    @pytest.mark.parametrize("cid,case", cases_for("ambient_temperature_celsius"))
    def test_ambient_temperature_celsius(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "ambient_temperature_celsius", assert_temp_range)

    @pytest.mark.parametrize("cid,case", cases_for("step_type"))
    def test_step_type(self, cid: str, case: SampleCase, data_dir: Path) -> None:
        run_check(case, data_dir, "step_type", assert_categorical)
