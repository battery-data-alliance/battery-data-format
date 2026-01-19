from __future__ import annotations

import pandas as pd

from bdf.time import parse_unix_time


def test_parse_unix_time_with_format() -> None:
    series = pd.Series(
        [
            "01/02/2024 03:04:05 PM",
            "01/02/2024 03:04:06 PM",
        ]
    )

    out = parse_unix_time(series, fmt="MM/DD/YYYY HH:MM:SS AM", tz="UTC", min_success=1.0)

    expected = pd.to_datetime(series, format="%m/%d/%Y %I:%M:%S %p", utc=True)
    assert float(out.iloc[0]) == expected.iloc[0].timestamp()
    assert float(out.iloc[1]) == expected.iloc[1].timestamp()
