"""Cross-check elapsed-time columns against independently recorded wall-clock time.

Vendor exports sometimes declare an elapsed-time column in seconds while the
values are actually in another unit (e.g. a Digatron export storing
milliseconds under a seconds header, GH issue #65). Nothing inside the column
itself reveals this: the timeline is self-consistent, so every single-column
check passes. The mismatch only becomes visible when the column's increments
are compared with the wall-clock (``unix_time_second``) increments recorded
independently alongside them.

This module holds the pure detection arithmetic shared by ``bdf.io.read``
(reconcile on read) and ``bdf.validate`` (report on validation).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["KNOWN_SCALE_FACTORS", "ScaleMismatch", "detect_scale_mismatch"]

# ratio of (elapsed increments / wall-clock increments) -> actual unit of the
# elapsed values when the header declares seconds.
KNOWN_SCALE_FACTORS: tuple[tuple[float, str], ...] = (
    (1e9, "nanoseconds"),
    (1e6, "microseconds"),
    (1e3, "milliseconds"),
    (1.0 / 60.0, "minutes"),
    (1.0 / 3600.0, "hours"),
)

# |ratio - 1| within this band counts as consistent. Wall-clock timestamps are
# typically recorded at 1 s resolution, so individual delta ratios jitter; the
# band is applied to the median over many samples.
_CONSISTENT_RTOL = 0.05
_FACTOR_RTOL = 0.05
_MIN_WALL_STEP_S = 0.5
_MIN_SAMPLES = 10


@dataclass(frozen=True)
class ScaleMismatch:
    """A detected disagreement between elapsed-time and wall-clock increments.

    Attributes:
        ratio: Median of (elapsed increment / wall-clock increment) over valid rows.
        factor: Matched entry from :data:`KNOWN_SCALE_FACTORS`, or None when the
            ratio matches no known unit (unexplained; do not auto-repair).
        unit_name: Human-readable name of the actual unit ("milliseconds", ...),
            or None when unexplained.
        n_samples: Number of increment pairs the ratio was estimated from.
    """

    ratio: float
    factor: float | None
    unit_name: str | None
    n_samples: int


def detect_scale_mismatch(
    elapsed: np.ndarray,
    wall: np.ndarray,
    *,
    min_wall_step: float = _MIN_WALL_STEP_S,
    min_samples: int = _MIN_SAMPLES,
) -> ScaleMismatch | None:
    """Compare elapsed-time increments with wall-clock increments.

    Both inputs are value arrays in declared seconds, row-aligned. Increments
    are compared pairwise; only rows where both clocks advance are used, so
    step-time resets and logging pauses drop out naturally. The median ratio
    is robust to a minority of irregular samples.

    Args:
        elapsed: Elapsed-time column values (e.g. ``Test Time / s``).
        wall: Wall-clock column values (``Unix Time / s``).
        min_wall_step: Minimum wall-clock increment (seconds) for a sample to
            count, guarding against timestamp-resolution noise.
        min_samples: Minimum number of valid increment pairs required to judge.

    Returns:
        A :class:`ScaleMismatch` when the median ratio deviates from 1 beyond
        tolerance (``factor``/``unit_name`` set when it matches a known unit),
        or None when the clocks agree or there is too little data to judge.
    """
    if elapsed.size != wall.size or elapsed.size < min_samples + 1:
        return None
    d_elapsed = np.diff(elapsed.astype(float))
    d_wall = np.diff(wall.astype(float))
    valid = np.isfinite(d_elapsed) & np.isfinite(d_wall) & (d_elapsed > 0) & (d_wall >= min_wall_step)
    n = int(valid.sum())
    if n < min_samples:
        return None
    ratio = float(np.median(d_elapsed[valid] / d_wall[valid]))
    if abs(ratio - 1.0) <= _CONSISTENT_RTOL:
        return None
    for factor, unit_name in KNOWN_SCALE_FACTORS:
        if abs(ratio - factor) <= _FACTOR_RTOL * factor:
            return ScaleMismatch(ratio=ratio, factor=factor, unit_name=unit_name, n_samples=n)
    return ScaleMismatch(ratio=ratio, factor=None, unit_name=None, n_samples=n)
