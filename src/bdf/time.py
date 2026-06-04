from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def parse_unix_time(
    series: pd.Series,
    *,
    fmt: str | None = None,
    tz: str | None = None,
    min_success: float = 0.5,
) -> pd.Series:
    """
    Convert a timestamp series to unix seconds.

    - Numeric series: auto-detect s/ms/us/ns by magnitude.
    - String series: parse with optional format and timezone.
    - If parse success < min_success, raise ValueError.
    """
    if not isinstance(series, pd.Series):
        series = pd.Series(series)

    s = series
    if pd.api.types.is_numeric_dtype(s):
        x = pd.to_numeric(s, errors="coerce").astype("float64")
        med_abs = float(np.nanmedian(np.abs(x))) if len(x) else np.nan
        if np.isfinite(med_abs):
            if med_abs >= 1e17:
                unit = "ns"
            elif med_abs >= 1e14:
                unit = "us"
            elif med_abs >= 1e11:
                unit = "ms"
            else:
                unit = "s"
        else:
            unit = "s"
        dt = pd.to_datetime(x, unit=unit, utc=True, errors="coerce")
        return _datetime_to_unix(dt, min_success=min_success)

    fmt_norm = _normalize_datetime_format(fmt) if fmt else None
    if fmt_norm:
        dt = pd.to_datetime(s, format=fmt_norm, errors="coerce")
    else:
        dt = pd.to_datetime(s, errors="coerce", utc=False)

    if getattr(dt.dtype, "tz", None) is None:
        dt = dt.dt.tz_localize(tz) if tz else dt.dt.tz_localize("UTC")
    else:
        if tz:
            dt = dt.dt.tz_convert(tz)

    dt = dt.dt.tz_convert("UTC")
    return _datetime_to_unix(dt, min_success=min_success)


def _datetime_to_unix(dt: pd.Series, *, min_success: float) -> pd.Series:
    if len(dt) == 0:
        return pd.Series([], dtype="float64")

    success = float(dt.notna().mean())
    if success < min_success:
        raise ValueError(
            f"Timestamp parse success rate {success:.2%} below threshold {min_success:.2%}."
        )

    valid = dt.notna().to_numpy()
    epoch_s = np.full(len(dt), np.nan, dtype="float64")
    if valid.any():
        # Use timedelta arithmetic to avoid pandas-version-dependent int64 unit
        # (pandas 2.x may store datetimes in ms/us/s resolution, not just ns,
        # so astype("int64") does not reliably give nanoseconds).
        epoch = pd.Timestamp("1970-01-01", tz="UTC")
        epoch_s[valid] = (
            (dt[valid] - epoch).dt.total_seconds().to_numpy(dtype="float64")
        )
    return pd.Series(epoch_s, index=dt.index, name="Unix Time / s")


def _normalize_datetime_format(fmt: Optional[str]) -> Optional[str]:
    if not fmt:
        return None
    if "%" in fmt:
        return fmt

    raw = fmt
    parts = raw.split()
    date_part = parts[0] if parts else raw
    time_part = " ".join(parts[1:]) if len(parts) > 1 else ""

    date_part = (
        date_part.replace("YYYY", "%Y")
        .replace("YY", "%y")
        .replace("DD", "%d")
        .replace("MM", "%m")
    )

    ampm = False
    if re_search_any(time_part, ["AM/PM", "AM", "PM", "am/pm", "am", "pm"]):
        ampm = True
        time_part = time_part.replace("AM/PM", "%p").replace("am/pm", "%p")
        time_part = time_part.replace("AM", "%p").replace("PM", "%p")
        time_part = time_part.replace("am", "%p").replace("pm", "%p")

    time_part = time_part.replace("SS", "%S")
    time_part = time_part.replace("HH", "%I" if ampm else "%H")
    time_part = time_part.replace("MM", "%M")

    if time_part:
        return f"{date_part} {time_part}".strip()
    return date_part.strip()


def re_search_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)
