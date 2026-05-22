"""Unit tests for src/bdf/normalize.py (new Polars-based normalizer)."""

from __future__ import annotations

import warnings
from typing import cast
from unittest.mock import patch

import polars as pl
import pytest
from pydantic import ValidationError

from bdf.normalizer import (
    DateTimeSyn,
    MetadataParser,
    Normalizer,
    ResolvedColumn,
    Syn,
    normalize,
)
from bdf.sources import Source


class TestSyn:
    def test_exemplar_property(self):
        assert Syn("Voltage-{unit}").exemplar == "Voltage-{unit}"

    @pytest.mark.parametrize(
        "header,expected",
        [
            ("test-time", True),
            ("Test-Time", True),
            ("TEST-TIME", True),
            ("  test-time  ", True),
            ("other", False),
        ],
    )
    def test_exact_match_case_insensitive(self, header, expected):
        assert Syn("test-time").exact_match(header) is expected

    @pytest.mark.parametrize(
        "header,bdf_unit,expected_scale,expected_offset",
        [
            ("Voltage-V", "V", 1.0, 0.0),
            ("Voltage-mV", "V", pytest.approx(0.001), 0.0),
            ("Current-mA", "A", pytest.approx(0.001), 0.0),
            ("Current-A", "A", 1.0, 0.0),
            ("Time-h", "s", pytest.approx(3600.0), 0.0),
            ("Time-min", "s", pytest.approx(60.0), 0.0),
            ("Pressure-kPa", "Pa", pytest.approx(1000.0), 0.0),
        ],
    )
    def test_match_with_unit_compatible(self, header, bdf_unit, expected_scale, expected_offset):
        if "Voltage" in header:
            result = Syn("Voltage-{unit}").match(header, bdf_unit)
        elif "Current" in header:
            result = Syn("Current-{unit}").match(header, bdf_unit)
        elif "Time" in header:
            result = Syn("Time-{unit}").match(header, bdf_unit)
        else:
            result = Syn("Pressure-{unit}").match(header, bdf_unit)
        assert result is not None
        scale, offset = result
        assert scale == expected_scale
        assert offset == expected_offset

    def test_match_with_unit_returns_none_incompatible(self):
        # Voltage header matched against current unit → incompatible dimensions
        assert Syn("Voltage-{unit}").match("Voltage-V", "A") is None

    def test_match_with_unit_returns_none_wrong_base(self):
        assert Syn("Voltage-{unit}").match("Current-A", "V") is None

    def test_match_no_unit_exact(self):
        result = Syn("Test-Time").match("Test-Time", "s")
        assert result == (1.0, 0.0)

    def test_match_no_unit_case_insensitive(self):
        result = Syn("test-time").match("Test-Time", "s")
        assert result == (1.0, 0.0)

    def test_match_no_unit_mismatch(self):
        assert Syn("Test-Time").match("Other", "s") is None

    def test_model_validate_string(self):
        s = Syn.model_validate("Voltage-{unit}")
        assert s.root == "Voltage-{unit}"

    def test_frozen(self):
        s = Syn("x")
        with pytest.raises(ValidationError):
            s.root = "y"


# DateTimeSyn


class TestDateTimeSyn:
    def test_construction(self):
        dts = DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))
        assert dts.syn.root == "Test-Time"
        assert dts.fmts == ("%H:%M:%S.%f",)

    def test_fmts_stored_as_tuple(self):
        dts = DateTimeSyn(syn=Syn("T"), fmts=("%H:%M:%S", "%Y-%m-%d"))
        assert isinstance(dts.fmts, tuple)
        assert len(dts.fmts) == 2

    def test_model_validate_dict(self):
        dts = DateTimeSyn.model_validate({"syn": "Test-Time", "fmts": ["%H:%M:%S.%f"]})
        assert dts.syn.root == "Test-Time"
        assert "%H:%M:%S.%f" in dts.fmts

    def test_frozen(self):
        dts = DateTimeSyn(syn=Syn("T"), fmts=("%H:%M:%S",))
        with pytest.raises(ValidationError):
            dts.fmts = ("%Y",)


# ResolvedColumn


class TestResolvedColumn:
    # --- from_column_map ---

    @pytest.mark.parametrize(
        "bdf_label, src_col, expected_mr, expected_scale",
        [
            ("Voltage / mV", "col_v", "voltage_volt", pytest.approx(0.001)),
            ("Voltage / V", "col_v", "voltage_volt", 1.0),
            ("Current / mA", "col_i", "current_ampere", pytest.approx(0.001)),
            ("Current / A", "col_i", "current_ampere", 1.0),
            ("Test Time / s", "col_t", "test_time_second", 1.0),
            ("Test Time / h", "col_t", "test_time_second", pytest.approx(3600.0)),
        ],
    )
    def test_from_column_map_unit_conversion(self, bdf_label, src_col, expected_mr, expected_scale):
        mr, rc = ResolvedColumn.from_column_map(bdf_label, src_col)
        assert mr == expected_mr
        assert rc.source_header == src_col
        assert rc.scale == expected_scale
        assert rc.offset == pytest.approx(0.0)

    def test_from_column_map_invalid_label_raises(self):
        with pytest.raises(ValueError, match="label base not found"):
            ResolvedColumn.from_column_map("NotReal / V", "col")

    def test_from_column_map_incompatible_unit_warns(self):
        with pytest.warns(UserWarning, match="not compatible"):
            mr, rc = ResolvedColumn.from_column_map("Voltage / A", "col_v")
        assert rc.scale == 1.0

    # --- from_synonyms ---

    def test_from_synonyms_matches_syn(self):
        syns = [Syn("Voltage-{unit}")]
        rc = ResolvedColumn.from_synonyms("Voltage-mV", "Voltage-mV", "V", syns)
        assert rc is not None
        assert rc.source_header == "Voltage-mV"
        assert rc.scale == pytest.approx(0.001)

    def test_from_synonyms_matches_datetimesyn(self):
        syns = [DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))]
        rc = ResolvedColumn.from_synonyms("Test-Time", "Test-Time", "s", syns)
        assert rc is not None
        assert rc.source_header == "Test-Time"
        assert "%H:%M:%S.%f" in rc.datetime_fmts

    def test_from_synonyms_no_match_returns_none(self):
        syns = [Syn("Voltage-{unit}")]
        assert ResolvedColumn.from_synonyms("Unknown", "Unknown", "V", syns) is None

    def test_from_synonyms_first_match_wins(self):
        syns = [Syn("Col-{unit}"), Syn("Col-mV")]
        rc = ResolvedColumn.from_synonyms("Col-mV", "Col-mV", "V", syns)
        assert rc is not None
        # First syn matches via {unit} → scale 0.001
        assert rc.scale == pytest.approx(0.001)

    # --- get_expr: numeric ---

    def test_get_expr_float_no_scale(self):
        rc = ResolvedColumn(source_header="Voltage-V")
        df = pl.DataFrame({"Voltage-V": [3.2, 3.3, 3.4]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out.columns == ["Voltage / V"]
        assert out["Voltage / V"].to_list() == pytest.approx([3.2, 3.3, 3.4])
        assert out["Voltage / V"].dtype == pl.Float64

    def test_get_expr_float_with_scale(self):
        rc = ResolvedColumn(source_header="v_mv", scale=0.001)
        df = pl.DataFrame({"v_mv": [1000.0, 2000.0]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out["Voltage / V"].to_list() == pytest.approx([1.0, 2.0])

    def test_get_expr_casts_string_to_float(self):
        rc = ResolvedColumn(source_header="v")
        df = pl.DataFrame({"v": ["3.5", "4.2"]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out["Voltage / V"].dtype == pl.Float64
        assert out["Voltage / V"].to_list() == pytest.approx([3.5, 4.2])

    def test_get_expr_int_dtype_for_cycle_count(self):
        rc = ResolvedColumn(source_header="cycle")
        df = pl.DataFrame({"cycle": ["1", "2", "3"]})
        out = df.select(rc.get_expr("cycle_count"))
        assert out["Cycle Count / 1"].dtype == pl.Int64
        assert out["Cycle Count / 1"].to_list() == [1, 2, 3]

    def test_get_expr_float_dtype_for_voltage(self):
        rc = ResolvedColumn(source_header="v")
        df = pl.DataFrame({"v": [1.0, 2.0]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out["Voltage / V"].dtype == pl.Float64

    def test_get_expr_aliases_to_bdf_label(self):
        rc = ResolvedColumn(source_header="my_voltage", scale=0.001)
        df = pl.DataFrame({"my_voltage": [1000.0]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert "Voltage / V" in out.columns

    # --- get_expr: duration string ---

    @pytest.mark.parametrize(
        "time_str, expected_seconds",
        [
            ("00:00:00.00", 0.0),
            ("00:00:01.00", 1.0),
            ("00:01:30.50", 90.5),
            ("01:00:00.00", 3600.0),
            ("25:30:00.00", 91800.0),  # >23h: str.to_duration can't handle; custom parser required
        ],
    )
    def test_get_expr_duration_string(self, time_str, expected_seconds):
        rc = ResolvedColumn(source_header="t", datetime_fmts=("%H:%M:%S.%f",))
        df = pl.DataFrame({"t": [time_str]})
        out = df.select(rc.get_expr("test_time_second"))
        assert out["Test Time / s"][0] == pytest.approx(expected_seconds)

    def test_get_expr_duration_string_elapsed_from_zero(self):
        rc = ResolvedColumn(source_header="t", datetime_fmts=("%H:%M:%S.%f",))
        df = pl.DataFrame({"t": ["00:00:00.00", "00:00:01.00", "00:00:02.00"]})
        out = df.select(rc.get_expr("test_time_second"))
        assert out["Test Time / s"].to_list() == pytest.approx([0.0, 1.0, 2.0])

    # --- get_expr: datetime strings → elapsed ---

    def test_get_expr_datetime_elapsed_seconds(self):
        rc = ResolvedColumn(source_header="ts", datetime_fmts=("%Y-%m-%d %H:%M:%S",))
        df = pl.DataFrame({"ts": ["2024-01-01 00:00:00", "2024-01-01 00:01:00", "2024-01-01 00:02:00"]})
        out = df.select(rc.get_expr("test_time_second"))
        assert out["Test Time / s"].to_list() == pytest.approx([0.0, 60.0, 120.0])

    # --- get_expr: datetime strings → unix time ---

    def test_get_expr_unix_time_absolute(self):
        rc = ResolvedColumn(source_header="ts", datetime_fmts=("%Y-%m-%d %H:%M:%S",))
        df = pl.DataFrame({"ts": ["2024-01-01 00:00:00", "2024-01-01 00:01:00"]})
        out = df.select(rc.get_expr("unix_time_second"))
        t0, t1 = out["Unix Time / s"].to_list()
        assert t1 - t0 == pytest.approx(60.0)
        assert t0 > 1_700_000_000  # sanity: after Nov 2023


# MetadataParser


class TestMetadataParser:
    def test_parse_matches_pattern(self):
        mp = MetadataParser(start_time=r"Start time:\s*(\S+)")
        result = mp.parse(["header line", "Start time: 2024-01-01T00:00:00", "data"])
        assert result["start_time"] == "2024-01-01T00:00:00"

    def test_parse_no_match_returns_empty(self):
        mp = MetadataParser(start_time=r"Start time:\s*(\S+)")
        assert mp.parse(["no match here"]) == {}

    def test_parse_first_match_per_key(self):
        mp = MetadataParser(start_time=r"Time:\s*(\S+)")
        result = mp.parse(["Time: first", "Time: second"])
        assert result["start_time"] == "first"

    def test_parse_strips_whitespace(self):
        mp = MetadataParser(start_time=r"Time:\s*(.+)")
        result = mp.parse(["Time:   2024-01-01   "])
        assert result["start_time"] == "2024-01-01"

    def test_parse_case_insensitive(self):
        mp = MetadataParser(start_time=r"start time:\s*(\S+)")
        result = mp.parse(["START TIME: 2024-01-01"])
        assert result["start_time"] == "2024-01-01"

    def test_none_pattern_not_compiled(self):
        mp = MetadataParser(start_time=None)
        # No pattern → no keys in compiled dict
        assert mp.parse(["anything"]) == {}

    def test_parse_empty_lines(self):
        mp = MetadataParser(start_time=r"Time:\s*(\S+)")
        assert mp.parse([]) == {}


# Normalizer


class TestNormalizerIter:
    def test_iter_yields_only_non_none(self):
        n = Normalizer(voltage_volt=[Syn("Voltage-{unit}")])
        items = list(n)
        assert len(items) == 1
        assert items[0][0] == "voltage_volt"

    def test_iter_declaration_order(self):
        n = Normalizer(
            voltage_volt=[Syn("Voltage-{unit}")],
            current_ampere=[Syn("Current-{unit}")],
            test_time_second=[DateTimeSyn(syn=Syn("T"), fmts=("%H:%M:%S",))],
        )
        names = [mr for mr, _ in n]
        # Declaration order: test_time_second, voltage_volt, current_ampere
        assert names.index("test_time_second") < names.index("voltage_volt")
        assert names.index("voltage_volt") < names.index("current_ampere")

    def test_iter_empty_normalizer(self):
        assert list(Normalizer()) == []


class TestNormalizerResolve:
    @pytest.fixture
    def basic_normalizer(self):
        return Normalizer(
            test_time_second=[DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))],
            voltage_volt=[Syn("Voltage-{unit}")],
            current_ampere=[Syn("Current-{unit}")],
        )

    def test_resolve_returns_resolved_columns(self, basic_normalizer):
        resolved = basic_normalizer.resolve(["Test-Time", "Voltage-V", "Current-mA"])
        assert set(resolved.keys()) == {"test_time_second", "voltage_volt", "current_ampere"}

    def test_resolve_correct_source_headers(self, basic_normalizer):
        resolved = basic_normalizer.resolve(["Test-Time", "Voltage-V", "Current-mA"])
        assert resolved["voltage_volt"].source_header == "Voltage-V"
        assert resolved["current_ampere"].source_header == "Current-mA"

    def test_resolve_unit_conversion_stored(self, basic_normalizer):
        resolved = basic_normalizer.resolve(["Voltage-mV"])
        assert resolved["voltage_volt"].scale == pytest.approx(0.001)

    def test_resolve_resolved_column_passthrough(self):
        rc = ResolvedColumn(source_header="my_col", scale=0.001)
        n = Normalizer(voltage_volt=rc)
        resolved = n.resolve(["my_col"])
        assert resolved["voltage_volt"] is rc

    def test_resolve_first_claim_wins(self):
        # voltage_volt declared before current_ampere; if both could match same header, first wins
        n = Normalizer(
            voltage_volt=[Syn("Col-{unit}")],
            current_ampere=[Syn("Col-{unit}")],
        )
        resolved = n.resolve(["Col-V"])
        assert "voltage_volt" in resolved
        assert "current_ampere" not in resolved

    def test_resolve_tilde_prefix_stripped(self):
        n = Normalizer(test_time_second=[Syn("Time[s]")])
        resolved = n.resolve(["~Time[s]"])
        assert "test_time_second" in resolved
        assert resolved["test_time_second"].source_header == "~Time[s]"

    def test_resolve_partial_headers(self, basic_normalizer):
        resolved = basic_normalizer.resolve(["Voltage-V"])
        assert "voltage_volt" in resolved
        assert "current_ampere" not in resolved

    def test_resolve_unknown_headers_ignored(self, basic_normalizer):
        resolved = basic_normalizer.resolve(["Voltage-V", "unknown_col_xyz"])
        assert "voltage_volt" in resolved
        assert len(resolved) == 1

    def test_resolve_empty_headers(self, basic_normalizer):
        assert basic_normalizer.resolve([]) == {}

    def test_resolve_multiple_synonyms_fallback(self):
        n = Normalizer(current_ampere=[Syn("Current-{unit}"), Syn("Amps-{unit}")])
        resolved = n.resolve(["Amps-mA"])
        assert "current_ampere" in resolved
        assert resolved["current_ampere"].scale == pytest.approx(0.001)


class TestNormalizerScore:
    @pytest.fixture
    def normalizer(self):
        return Normalizer(
            test_time_second=[DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))],
            voltage_volt=[Syn("Voltage-{unit}")],
            current_ampere=[Syn("Current-{unit}")],
        )

    def test_score_all_match(self, normalizer):
        assert normalizer.score(["Test-Time", "Voltage-V", "Current-mA"]) == 3

    def test_score_partial_match(self, normalizer):
        assert normalizer.score(["Voltage-V"]) == 1

    def test_score_no_match(self, normalizer):
        assert normalizer.score(["unknown_col"]) == 0

    def test_score_incompatible_unit_reduces_score(self, normalizer):
        # Voltage-V but matched against current (A) unit → no match
        assert normalizer.score(["Voltage-V", "Current-V"]) == 1  # Current-V has wrong dim

    def test_score_extra_irrelevant_columns_ignored(self, normalizer):
        score = normalizer.score(["Test-Time", "Voltage-V", "Current-mA", "extra_col"])
        assert score == 3

    def test_score_with_resolved_column(self):
        n = Normalizer(voltage_volt=ResolvedColumn(source_header="my_v"))
        assert n.score(["my_v"]) == 1
        assert n.score(["other"]) == 0


class TestNormalizerNormalize:
    @pytest.fixture
    def normalizer(self):
        return Normalizer(
            test_time_second=[DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))],
            voltage_volt=[Syn("Voltage-{unit}")],
            current_ampere=[Syn("Current-{unit}")],
        )

    @pytest.fixture
    def simple_df(self):
        return pl.DataFrame(
            {
                "Test-Time": ["00:00:00.00", "00:00:01.00", "00:00:02.00"],
                "Voltage-V": [3.2, 3.3, 3.4],
                "Current-mA": [10.0, 10.0, 10.0],
            }
        )

    def test_normalize_returns_bdf_column_names(self, normalizer, simple_df):
        out = normalizer.normalize(simple_df)
        assert "Test Time / s" in out.columns
        assert "Voltage / V" in out.columns
        assert "Current / A" in out.columns

    def test_normalize_unit_conversion(self, normalizer, simple_df):
        out = normalizer.normalize(simple_df)
        assert out["Current / A"].to_list() == pytest.approx([0.01, 0.01, 0.01])

    def test_normalize_duration_string_to_seconds(self, normalizer, simple_df):
        out = normalizer.normalize(simple_df)
        assert out["Test Time / s"].to_list() == pytest.approx([0.0, 1.0, 2.0])

    def test_normalize_dataframe_returns_dataframe(self, normalizer, simple_df):
        assert isinstance(normalizer.normalize(simple_df), pl.DataFrame)

    def test_normalize_lazyframe_returns_lazyframe(self, normalizer, simple_df):
        lf = simple_df.lazy()
        out = normalizer.normalize(lf)
        assert isinstance(out, pl.LazyFrame)
        assert "Voltage / V" in out.collect().columns

    @pytest.mark.filterwarnings("ignore::UserWarning")
    def test_normalize_include_optional_false_excludes_optional(self):
        n = Normalizer(
            test_time_second=[Syn("t")],
            voltage_volt=[Syn("v")],
            current_ampere=[Syn("i")],
            cycle_count=[Syn("cycle")],
        )
        df = pl.DataFrame({"t": [1.0], "v": [3.5], "i": [0.1], "cycle": [1.0]})
        out = n.normalize(df, include_optional=False)
        assert "Test Time / s" in out.columns
        assert "Cycle Count / 1" not in out.columns

    def test_normalize_no_exprs_returns_df_unchanged(self):
        n = Normalizer(voltage_volt=[Syn("Voltage-{unit}")])
        df = pl.DataFrame({"unrelated": [1.0, 2.0]})
        out = n.normalize(df)
        assert out is df

    def test_normalize_column_map_override(self, simple_df):
        n = Normalizer(test_time_second=[DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))])
        out = n.normalize(simple_df, column_map={"Voltage / mV": "Voltage-V"})
        assert out["Voltage / V"][0] == pytest.approx(3.2 * 0.001)

    def test_normalize_column_map_invalid_key_raises(self, simple_df):
        n = Normalizer()
        with pytest.raises(ValueError, match="label base not found"):
            n.normalize(simple_df, column_map={"NotReal / V": "Voltage-V"})

    def test_normalize_extra_columns_passthrough(self, simple_df):
        n = Normalizer(voltage_volt=[Syn("Voltage-{unit}")])
        out = n.normalize(simple_df, extra_columns={"Test-Time": "raw_time"})
        assert "raw_time" in out.columns
        assert out["raw_time"].to_list() == simple_df["Test-Time"].to_list()

    def test_normalize_extra_columns_missing_warns(self, simple_df):
        n = Normalizer(voltage_volt=[Syn("Voltage-{unit}")])
        with pytest.warns(UserWarning, match="not in DataFrame"):
            n.normalize(simple_df, extra_columns={"ghost_col": "Out"})

    def test_normalize_missing_required_warns(self):
        n = Normalizer(voltage_volt=[Syn("v")])
        df = pl.DataFrame({"v": [3.5]})
        with pytest.warns(UserWarning, match="required BDF columns missing"):
            n.normalize(df)

    @pytest.mark.parametrize(
        "col, bdf_unit, header, value, expected",
        [
            ("Voltage-mV", "V", "Voltage / V", 1000.0, pytest.approx(1.0)),
            ("Current-mA", "A", "Current / A", 500.0, pytest.approx(0.5)),
            ("Time-h", "s", "Test Time / s", 1.0, pytest.approx(3600.0)),
        ],
    )
    def test_normalize_unit_conversion_parametrized(self, col, bdf_unit, header, value, expected):
        field_map = {
            "V": "voltage_volt",
            "A": "current_ampere",
            "s": "test_time_second",
        }
        syn_map: dict[str, list[Syn | DateTimeSyn] | ResolvedColumn | None] = {
            "voltage_volt": cast(list[Syn | DateTimeSyn], [Syn("Voltage-{unit}")]),
            "current_ampere": cast(list[Syn | DateTimeSyn], [Syn("Current-{unit}")]),
            "test_time_second": cast(list[Syn | DateTimeSyn], [Syn("Time-{unit}")]),
        }
        mr = field_map[bdf_unit]
        n = Normalizer(**{mr: syn_map[mr]})
        df = pl.DataFrame({col: [value]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df)
        assert out[header][0] == expected

    def test_normalize_int_dtype_cycle_count(self):
        n = Normalizer(cycle_count=[Syn("cycle")])
        df = pl.DataFrame({"cycle": [1.0, 2.0, 3.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df)
        assert out["Cycle Count / 1"].dtype == pl.Int64

    def test_normalize_float_dtype_voltage(self):
        n = Normalizer(voltage_volt=[Syn("v")])
        df = pl.DataFrame({"v": [3.5]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df)
        assert out["Voltage / V"].dtype == pl.Float64


class TestNormalizerModelValidate:
    def test_json_validation_synonym_list(self):
        n = Normalizer.model_validate(
            {
                "test_time_second": [{"syn": "Test-Time", "fmts": ["%H:%M:%S.%f"]}],
                "voltage_volt": ["Voltage-{unit}"],
                "current_ampere": ["Current-{unit}"],
            }
        )
        assert n.score(["Test-Time", "Voltage-V", "Current-mA"]) == 3

    def test_json_validation_resolved_column(self):
        n = Normalizer.model_validate(
            {
                "voltage_volt": {"source_header": "my_v", "scale": 0.001},
            }
        )
        resolved = n.resolve(["my_v"])
        assert resolved["voltage_volt"].scale == pytest.approx(0.001)


# Top-level normalize() function


class TestNormalizeFn:
    def test_no_source_no_column_map_no_extra_returns_df(self):
        df = pl.DataFrame({"unknown_col": [1.0, 2.0]})
        with patch("bdf.normalizer._detect_source", return_value=None):
            out = normalize(df)
        assert out is df

    def test_column_map_only_no_source(self):
        df = pl.DataFrame({"my_v": [1000.0]})
        with patch("bdf.normalizer._detect_source", return_value=None), warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(df, column_map={"Voltage / mV": "my_v"})
        assert "Voltage / V" in out.columns
        assert out["Voltage / V"][0] == pytest.approx(1.0)

    def test_extra_columns_only_no_source(self):
        df = pl.DataFrame({"raw": [1.0, 2.0]})
        with patch("bdf.normalizer._detect_source", return_value=None):
            out = normalize(df, extra_columns={"raw": "Raw Out"})
        assert "Raw Out" in out.columns

    def test_lazyframe_passthrough_unchanged(self):
        lf = pl.LazyFrame({"unknown_xyz": [1.0]})
        with patch("bdf.normalizer._detect_source", return_value=None):
            out = normalize(lf)
        assert isinstance(out, pl.LazyFrame)
        assert out is lf

    def test_source_object_uses_its_normalizer(self):
        src = Source(id="test_src", normalizer=Normalizer(voltage_volt=[Syn("v")]))
        df = pl.DataFrame({"v": [3.5]})
        with patch("bdf.normalizer._detect_source", return_value=None), warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(df, source=src)
        assert "Voltage / V" in out.columns
