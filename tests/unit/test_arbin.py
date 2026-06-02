from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest

import bdf
from bdf.data_sources.arbin import ArbinCSV, ArbinExcel

DATA = Path(__file__).parent.parent / "data"
CSV = DATA / "tiny_arbin.csv"
XLSX = DATA / "tiny_arbin.xlsx"
XLSX_MULTISHEET = DATA / "tiny_arbin_multisheet.xlsx"


def _frame(result) -> pd.DataFrame:
    if isinstance(result, dict):
        return next(iter(result.values()))
    return result


# ---------------------------------------------------------------------------
# Format A — CSV
# ---------------------------------------------------------------------------

def test_arbin_csv_sniff_identifies_format_a() -> None:
    plugin = ArbinCSV()
    head = CSV.read_bytes()[:4096]
    sniff = plugin.sniff(CSV, head)
    assert sniff.id == "arbin-csv"
    # ext + Arbin header tokens should give a confident match.
    assert sniff.confidence >= 0.5


def test_arbin_csv_autodetects_over_other_csv_plugins() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _frame(bdf.read(CSV))
    assert {"Test Time / s", "Voltage / V", "Current / A"}.issubset(df.columns)
    # Both charge and discharge half-cycles present.
    current = pd.to_numeric(df["Current / A"], errors="coerce")
    assert (current > 0).any() and (current < 0).any()


def test_arbin_csv_date_time_yields_unix_time() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _frame(bdf.read(CSV))
    assert "Unix Time / s" in df.columns
    unix = pd.to_numeric(df["Unix Time / s"], errors="coerce").dropna()
    assert len(unix) == 5
    assert (unix.diff().dropna() > 0).all()


# ---------------------------------------------------------------------------
# Format B — XLSX
# ---------------------------------------------------------------------------

def test_arbin_xlsx_sniff_beats_generic_excel() -> None:
    plugin = ArbinExcel()
    head = XLSX.read_bytes()[:64]
    sniff = plugin.sniff(XLSX, head)
    assert sniff.id == "arbin-xlsx"
    assert sniff.meta.get("arbin") is True
    assert sniff.confidence >= 0.8


def test_arbin_xlsx_autodetects_and_normalizes() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _frame(bdf.read(XLSX))
    assert {"Test Time / s", "Voltage / V", "Current / A"}.issubset(df.columns)
    assert "Step ID" in df.columns
    # Degree-symbol auxiliary temperature headers normalized to both channels.
    assert "Surface Temperature T1 / degC" in df.columns
    assert "Surface Temperature T2 / degC" in df.columns


def test_arbin_xlsx_plugin_can_be_selected_explicitly() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _frame(bdf.read(XLSX, plugin="arbin-xlsx"))
    assert {"Test Time / s", "Voltage / V", "Current / A"}.issubset(df.columns)


# ---------------------------------------------------------------------------
# Capacity / energy re-accumulation across instrument counter resets
# ---------------------------------------------------------------------------

def test_arbin_reaccumulates_accumulators_across_reset() -> None:
    """Arbin charge/discharge accumulators may reset to 0 at a schedule step;
    fixup() must re-integrate them into a monotonic, never-resetting series."""
    df = pd.DataFrame({
        "Charging Capacity / Ah":    [0.0, 0.5, 1.0, 0.0, 0.2, 0.4],   # reset at idx 3
        "Discharging Capacity / Ah": [0.0, 0.0, 0.0, 0.0, 0.3, 0.6],
        "Charging Energy / Wh":      [0.0, 2.0, 4.0, 0.0, 0.8, 1.6],   # reset at idx 3
    })
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = ArbinCSV().fixup(df)

    cc = out["Charging Capacity / Ah"]
    assert (cc.diff().dropna() >= -1e-12).all(), "charge capacity not monotonic after fixup"
    # banked pre-reset value (1.0) carries forward: 0, .5, 1, 1, 1.2, 1.4
    assert cc.iloc[2] == pytest.approx(1.0)
    assert cc.iloc[3] == pytest.approx(1.0)
    assert cc.iloc[-1] == pytest.approx(1.4)

    ce = out["Charging Energy / Wh"]
    assert (ce.diff().dropna() >= -1e-12).all()
    assert ce.iloc[-1] == pytest.approx(5.6)


def test_arbin_fixup_is_noop_when_already_monotonic() -> None:
    """No reset -> instrument values are preserved unchanged (no spurious edits)."""
    df = pd.DataFrame({"Charging Capacity / Ah": [0.0, 0.5, 1.0, 1.5, 2.0]})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = ArbinCSV().fixup(df)
    pd.testing.assert_series_equal(
        out["Charging Capacity / Ah"], df["Charging Capacity / Ah"], check_names=True
    )


def test_arbin_fixup_emits_warning_only_on_reset() -> None:
    reset = pd.DataFrame({"Charging Capacity / Ah": [0.0, 1.0, 0.0, 0.5]})
    clean = pd.DataFrame({"Charging Capacity / Ah": [0.0, 1.0, 2.0, 3.0]})

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ArbinCSV().fixup(reset)
    assert any("re-accumulated" in str(x.message) for x in w)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ArbinCSV().fixup(clean)
    assert not any("re-accumulated" in str(x.message) for x in w)


def test_arbin_xlsx_selects_data_sheet_in_multisheet_workbook() -> None:
    """Real Arbin XLSX exports put a 'Global_Info' metadata sheet first and the
    time-series on a later 'Channel_N' sheet; the reader must find the data sheet."""
    plugin = ArbinExcel()
    sheet, headers = plugin._find_data_sheet(XLSX_MULTISHEET)
    assert sheet == "Channel_1_1", f"expected data sheet, got {sheet!r}"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _frame(bdf.read(XLSX_MULTISHEET))
    assert {"Test Time / s", "Voltage / V", "Current / A"}.issubset(df.columns)
    assert len(df) == 5
    assert "Surface Temperature T1 / degC" in df.columns
