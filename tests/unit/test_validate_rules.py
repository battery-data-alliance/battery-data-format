"""Term-rule validation: data must adhere to the definitional rules of each
canonical quantity (monotonic accumulators, non-negative magnitudes, signed
running integrals bounded by throughput)."""
from __future__ import annotations

import warnings

import pandas as pd

from bdf.validate import check_term_rules, validate_df


def _rules(extra: dict) -> list[dict]:
    return check_term_rules(pd.DataFrame(extra))


def test_conforming_data_has_no_violations():
    df = {
        "Charging Capacity / Ah":   [0.0, 0.5, 1.0, 1.0],
        "Discharging Capacity / Ah": [0.0, 0.0, 0.0, 0.4],
        "Cumulative Capacity / Ah": [0.0, 0.5, 1.0, 1.4],
        "Net Capacity / Ah":        [0.0, 0.5, 1.0, 0.6],
        "Step Capacity / Ah":       [0.0, 0.5, 0.0, 0.4],
        "Cycle Count / 1":          [1, 1, 2, 2],
    }
    assert _rules(df) == []


def test_monotonic_violation_flagged():
    v = _rules({"Cumulative Capacity / Ah": [0.0, 1.0, 0.5, 1.5]})
    assert len(v) == 1
    assert v[0]["column"] == "Cumulative Capacity / Ah"
    assert v[0]["rule"] == "monotonic non-decreasing"
    assert v[0]["violations"] == 1


def test_non_negative_violation_flagged():
    # Step Capacity is an unsigned magnitude; negatives are a sign-convention bug.
    v = _rules({"Step Capacity / Ah": [0.0, 0.3, -0.2, 0.1]})
    assert any(x["column"] == "Step Capacity / Ah" and x["rule"] == "non-negative" for x in v)


def test_net_cannot_exceed_cumulative():
    v = _rules({
        "Cumulative Capacity / Ah": [0.0, 1.0, 2.0],
        "Net Capacity / Ah":        [0.0, 1.5, 2.0],  # 1.5 > 1.0 -> violation
    })
    assert any(x["column"] == "Net Capacity / Ah" for x in v)


def test_net_may_be_negative_within_bound():
    # Net is signed; a negative value within the throughput bound is valid.
    assert _rules({
        "Cumulative Capacity / Ah": [0.0, 1.0, 2.0],
        "Net Capacity / Ah":        [0.0, -0.5, -1.0],
    }) == []


def test_float_noise_not_flagged():
    # Tiny non-monotonic wobble near a large running value is numerical noise.
    s = [float(x) for x in range(0, 1000)]
    s[500] -= 1e-9
    assert _rules({"Cumulative Capacity / Ah": s}) == []


def test_validate_df_warns_on_rule_violation():
    df = pd.DataFrame({
        "Test Time / s": [0, 1, 2, 3],
        "Voltage / V":   [3.7, 3.6, 3.5, 3.4],
        "Current / A":   [-1.0, -1.0, -1.0, -1.0],
        "Cumulative Capacity / Ah": [0.0, 1.0, 0.5, 1.5],  # decreases -> violation
    })
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rep = validate_df(df, raise_on_error=False)
    assert any("Term-rule violation" in str(w.message) for w in caught)
    assert rep["rule_violations"]


def test_validate_df_quiet_on_conforming_data():
    df = pd.DataFrame({
        "Test Time / s": [0, 1, 2, 3],
        "Voltage / V":   [3.7, 3.6, 3.5, 3.4],
        "Current / A":   [-1.0, -1.0, -1.0, -1.0],
        "Cumulative Capacity / Ah": [0.0, 1.0, 2.0, 3.0],
    })
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rep = validate_df(df, raise_on_error=False)
    assert not any("Term-rule violation" in str(w.message) for w in caught)
    assert rep["rule_violations"] == []
