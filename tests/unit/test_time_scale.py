from __future__ import annotations

import numpy as np

from bdf._time_scale import detect_scale_mismatch


def _wall(n: int, step: float = 10.0) -> np.ndarray:
    return 1.7e9 + np.arange(n, dtype=float) * step


def test_consistent_clocks_return_none() -> None:
    n = 50
    assert detect_scale_mismatch(np.arange(n, dtype=float) * 10.0, _wall(n)) is None


def test_milliseconds_labeled_seconds_detected() -> None:
    n = 50
    elapsed_ms = np.arange(n, dtype=float) * 10.0 * 1e3
    mismatch = detect_scale_mismatch(elapsed_ms, _wall(n))
    assert mismatch is not None
    assert mismatch.factor == 1e3
    assert mismatch.unit_name == "milliseconds"
    assert mismatch.ratio == 1e3


def test_minutes_labeled_seconds_detected() -> None:
    n = 50
    elapsed_min = np.arange(n, dtype=float) * 10.0 / 60.0
    mismatch = detect_scale_mismatch(elapsed_min, _wall(n))
    assert mismatch is not None
    assert mismatch.unit_name == "minutes"


def test_wall_clock_resolution_jitter_still_detected() -> None:
    # wall clock recorded at 1 s resolution: increments of 10 s jitter to 9/10/11
    rng = np.random.default_rng(42)
    n = 200
    wall = 1.7e9 + np.cumsum(rng.choice([9.0, 10.0, 11.0], size=n))
    elapsed_ms = np.cumsum(np.full(n, 10.0)) * 1e3
    mismatch = detect_scale_mismatch(elapsed_ms, wall)
    assert mismatch is not None
    assert mismatch.factor == 1e3


def test_unexplained_ratio_has_no_factor() -> None:
    n = 50
    mismatch = detect_scale_mismatch(np.arange(n, dtype=float) * 37.0, _wall(n))
    assert mismatch is not None
    assert mismatch.factor is None
    assert mismatch.unit_name is None


def test_too_few_samples_return_none() -> None:
    n = 5
    assert detect_scale_mismatch(np.arange(n, dtype=float) * 1e4, _wall(n)) is None


def test_step_time_resets_are_filtered_out() -> None:
    # step time in ms, resetting to 0 every 10 rows; resets produce negative
    # diffs that must not pollute the estimate
    n = 100
    step_ms = (np.arange(n, dtype=float) % 10) * 10.0 * 1e3
    mismatch = detect_scale_mismatch(step_ms, _wall(n))
    assert mismatch is not None
    assert mismatch.factor == 1e3


def test_pauses_in_elapsed_time_do_not_false_positive() -> None:
    # elapsed clock stops (paused test) while wall clock advances for a
    # minority of rows; the median must stay on the consistent samples
    n = 100
    elapsed = np.cumsum(np.where(np.arange(n) % 10 == 0, 0.0, 10.0))
    wall = _wall(n)
    assert detect_scale_mismatch(elapsed, wall) is None


def test_nans_are_ignored() -> None:
    n = 60
    elapsed_ms = np.arange(n, dtype=float) * 10.0 * 1e3
    elapsed_ms[::7] = np.nan
    mismatch = detect_scale_mismatch(elapsed_ms, _wall(n))
    assert mismatch is not None
    assert mismatch.factor == 1e3
