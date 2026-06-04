"""Unit tests for src/bdf/normalize.py (new Polars-based normalizer)."""

from __future__ import annotations

import warnings
from typing import cast

import polars as pl
import pytest
from pydantic import ValidationError

from bdf.normalizers import (
    DateTimeSyn,
    ResolvedColumn,
    Syn,
    TableNormalizer,
    normalize,
)


class TestSyn:
    def test_exemplar_property(self):
        """Exemplar property returns the root synonym pattern."""
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
        """exact_match is case-insensitive and ignores surrounding whitespace."""
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
        """match extracts unit from header and returns correct scale and offset for compatible units."""
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
        """match returns None when header's dimension is incompatible with bdf_unit."""
        assert Syn("Voltage-{unit}").match("Voltage-V", "A") is None

    def test_match_with_unit_returns_none_wrong_base(self):
        """match returns None when header base doesn't match synonym."""
        assert Syn("Voltage-{unit}").match("Current-A", "V") is None

    def test_match_no_unit_exact(self):
        """match on pattern without {unit} returns (1.0, 0.0) for exact case-insensitive match."""
        result = Syn("Test-Time").match("Test-Time", "s")
        assert result == (1.0, 0.0)

    def test_match_no_unit_case_insensitive(self):
        """match without {unit} placeholder is case-insensitive."""
        result = Syn("test-time").match("Test-Time", "s")
        assert result == (1.0, 0.0)

    def test_match_no_unit_mismatch(self):
        """match without {unit} returns None when header doesn't match."""
        assert Syn("Test-Time").match("Other", "s") is None

    def test_model_validate_string(self):
        """model_validate coerces string argument to Syn root model."""
        s = Syn.model_validate("Voltage-{unit}")
        assert s.root == "Voltage-{unit}"

    def test_frozen(self):
        """Syn is frozen and cannot be mutated after creation."""
        s = Syn("x")
        with pytest.raises(ValidationError):
            s.root = "y"


# DateTimeSyn


class TestDateTimeSyn:
    def test_construction(self):
        """DateTimeSyn stores syn and fmts during construction."""
        dts = DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))
        assert dts.syn.root == "Test-Time"
        assert dts.fmts == ("%H:%M:%S.%f",)

    def test_fmts_stored_as_tuple(self):
        """DateTimeSyn converts fmts list to tuple."""
        dts = DateTimeSyn(syn=Syn("T"), fmts=("%H:%M:%S", "%Y-%m-%d"))
        assert isinstance(dts.fmts, tuple)
        assert len(dts.fmts) == 2

    def test_model_validate_dict(self):
        """model_validate accepts dict with string syn and list fmts."""
        dts = DateTimeSyn.model_validate({"syn": "Test-Time", "fmts": ["%H:%M:%S.%f"]})
        assert dts.syn.root == "Test-Time"
        assert "%H:%M:%S.%f" in dts.fmts

    def test_frozen(self):
        """DateTimeSyn is frozen and cannot be mutated after creation."""
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
        """from_column_map converts BDF label to mr_name and applies unit scaling."""
        mr, rc = ResolvedColumn.from_column_map(bdf_label, src_col)
        assert mr == expected_mr
        assert rc.source_header == src_col
        assert rc.scale == expected_scale
        assert rc.offset == pytest.approx(0.0)

    def test_from_column_map_invalid_label_raises(self):
        """from_column_map raises ValueError for unknown BDF label."""
        with pytest.raises(ValueError, match="label base not found"):
            ResolvedColumn.from_column_map("NotReal / V", "col")

    def test_from_column_map_incompatible_unit_warns(self):
        """from_column_map warns on incompatible unit and uses scale 1.0."""
        with pytest.warns(UserWarning, match="not compatible"):
            mr, rc = ResolvedColumn.from_column_map("Voltage / A", "col_v")
        assert rc.scale == 1.0

    # --- from_synonyms ---

    def test_from_synonyms_matches_syn(self):
        """from_synonyms matches Syn and returns ResolvedColumn with scale conversion."""
        syns = [Syn("Voltage-{unit}")]
        rc = ResolvedColumn.from_synonyms("Voltage-mV", "Voltage-mV", "V", syns)
        assert rc is not None
        assert rc.source_header == "Voltage-mV"
        assert rc.scale == pytest.approx(0.001)

    def test_from_synonyms_matches_datetimesyn(self):
        """from_synonyms matches DateTimeSyn and stores format strings."""
        syns = [DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",))]
        rc = ResolvedColumn.from_synonyms("Test-Time", "Test-Time", "s", syns)
        assert rc is not None
        assert rc.source_header == "Test-Time"
        assert "%H:%M:%S.%f" in rc.datetime_fmts

    def test_from_synonyms_no_match_returns_none(self):
        """from_synonyms returns None when no synonym matches."""
        syns = [Syn("Voltage-{unit}")]
        assert ResolvedColumn.from_synonyms("Unknown", "Unknown", "V", syns) is None

    def test_from_synonyms_first_match_wins(self):
        """from_synonyms returns first matching synonym, stops checking."""
        syns = [Syn("Col-{unit}"), Syn("Col-mV")]
        rc = ResolvedColumn.from_synonyms("Col-mV", "Col-mV", "V", syns)
        assert rc is not None
        assert rc.scale == pytest.approx(0.001)

    # --- get_expr: numeric ---

    def test_get_expr_float_no_scale(self):
        """get_expr returns Float64 column with BDF label when no scaling needed."""
        rc = ResolvedColumn(source_header="Voltage-V")
        df = pl.DataFrame({"Voltage-V": [3.2, 3.3, 3.4]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out.columns == ["Voltage / V"]
        assert out["Voltage / V"].to_list() == pytest.approx([3.2, 3.3, 3.4])
        assert out["Voltage / V"].dtype == pl.Float64

    def test_get_expr_float_with_scale(self):
        """get_expr applies scale factor to numeric values."""
        rc = ResolvedColumn(source_header="v_mv", scale=0.001)
        df = pl.DataFrame({"v_mv": [1000.0, 2000.0]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out["Voltage / V"].to_list() == pytest.approx([1.0, 2.0])

    def test_get_expr_casts_string_to_float(self):
        """get_expr casts string columns to Float64."""
        rc = ResolvedColumn(source_header="v")
        df = pl.DataFrame({"v": ["3.5", "4.2"]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out["Voltage / V"].dtype == pl.Float64
        assert out["Voltage / V"].to_list() == pytest.approx([3.5, 4.2])

    def test_get_expr_int_dtype_for_cycle_count(self):
        """get_expr returns Int64 for integer-type BDF columns."""
        rc = ResolvedColumn(source_header="cycle")
        df = pl.DataFrame({"cycle": ["1", "2", "3"]})
        out = df.select(rc.get_expr("cycle_count"))
        assert out["Cycle Count / 1"].dtype == pl.Int64
        assert out["Cycle Count / 1"].to_list() == [1, 2, 3]

    def test_get_expr_float_dtype_for_voltage(self):
        """get_expr returns Float64 for float-type BDF columns."""
        rc = ResolvedColumn(source_header="v")
        df = pl.DataFrame({"v": [1.0, 2.0]})
        out = df.select(rc.get_expr("voltage_volt"))
        assert out["Voltage / V"].dtype == pl.Float64

    def test_get_expr_aliases_to_bdf_label(self):
        """get_expr aliases column to the BDF canonical label."""
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
        """get_expr parses HH:MM:SS.ff duration strings to elapsed seconds."""
        rc = ResolvedColumn(source_header="t", datetime_fmts=("%H:%M:%S.%f",))
        df = pl.DataFrame({"t": [time_str]})
        out = df.select(rc.get_expr("test_time_second"))
        assert out["Test Time / s"][0] == pytest.approx(expected_seconds)

    def test_get_expr_duration_string_elapsed_from_zero(self):
        """get_expr computes elapsed time from first row for duration strings."""
        rc = ResolvedColumn(source_header="t", datetime_fmts=("%H:%M:%S.%f",))
        df = pl.DataFrame({"t": ["00:00:00.00", "00:00:01.00", "00:00:02.00"]})
        out = df.select(rc.get_expr("test_time_second"))
        assert out["Test Time / s"].to_list() == pytest.approx([0.0, 1.0, 2.0])

    # --- get_expr: datetime strings → elapsed ---

    def test_get_expr_datetime_elapsed_seconds(self):
        """get_expr computes elapsed seconds since first datetime row."""
        rc = ResolvedColumn(source_header="ts", datetime_fmts=("%Y-%m-%d %H:%M:%S",))
        df = pl.DataFrame({"ts": ["2024-01-01 00:00:00", "2024-01-01 00:01:00", "2024-01-01 00:02:00"]})
        out = df.select(rc.get_expr("test_time_second"))
        assert out["Test Time / s"].to_list() == pytest.approx([0.0, 60.0, 120.0])

    # --- get_expr: datetime strings → unix time ---

    def test_get_expr_unix_time_absolute(self):
        """get_expr converts datetimes to unix timestamp seconds."""
        rc = ResolvedColumn(source_header="ts", datetime_fmts=("%Y-%m-%d %H:%M:%S",))
        df = pl.DataFrame({"ts": ["2024-01-01 00:00:00", "2024-01-01 00:01:00"]})
        out = df.select(rc.get_expr("unix_time_second"))
        t0, t1 = out["Unix Time / s"].to_list()
        assert t1 - t0 == pytest.approx(60.0)
        assert t0 > 1_700_000_000  # sanity: after Nov 2023


# TableNormalizer hashability (synonym fields are tuples)


def test_normalizer_is_hashable() -> None:
    """A TableNormalizer with synonym fields is hashable and can live in a frozenset."""
    n = TableNormalizer(voltage_volt=(Syn("voltage"),), current_ampere=(Syn("current"),))
    assert n in frozenset({n})


def test_normalizer_synonym_field_is_tuple() -> None:
    """Tuple input is stored as a tuple (order preserved)."""
    n = TableNormalizer(voltage_volt=(Syn("a"), Syn("b")))
    assert isinstance(n.voltage_volt, tuple)
    assert [cast(Syn, s).exemplar for s in n.voltage_volt] == ["a", "b"]


# TableNormalizer


class TestNormalizerIter:
    def test_iter_yields_only_non_none(self):
        """__iter__ yields only non-None fields."""
        n = TableNormalizer(voltage_volt=(Syn("Voltage-{unit}"),))
        items = list(n)
        assert len(items) == 1
        assert items[0][0] == "voltage_volt"

    def test_iter_declaration_order(self):
        """__iter__ yields fields in declaration order."""
        n = TableNormalizer(
            voltage_volt=(Syn("Voltage-{unit}"),),
            current_ampere=(Syn("Current-{unit}"),),
            test_time_second=(DateTimeSyn(syn=Syn("T"), fmts=("%H:%M:%S",)),),
        )
        names = [mr for mr, _ in n]
        assert names.index("test_time_second") < names.index("voltage_volt")
        assert names.index("voltage_volt") < names.index("current_ampere")

    def test_iter_empty_normalizer(self):
        """__iter__ on empty TableNormalizer yields no items."""
        assert list(TableNormalizer()) == []


class TestNormalizerResolve:
    @pytest.fixture
    def basic_normalizer(self):
        return TableNormalizer(
            test_time_second=(DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn("Voltage-{unit}"),),
            current_ampere=(Syn("Current-{unit}"),),
        )

    def test_resolve_returns_resolved_columns(self, basic_normalizer):
        """resolve returns dict mapping mr_name to ResolvedColumn for matching headers."""
        resolved = basic_normalizer.resolve(["Test-Time", "Voltage-V", "Current-mA"])
        assert set(resolved.keys()) == {"test_time_second", "voltage_volt", "current_ampere"}

    def test_resolve_correct_source_headers(self, basic_normalizer):
        """resolve stores correct source header in each ResolvedColumn."""
        resolved = basic_normalizer.resolve(["Test-Time", "Voltage-V", "Current-mA"])
        assert resolved["voltage_volt"].source_header == "Voltage-V"
        assert resolved["current_ampere"].source_header == "Current-mA"

    def test_resolve_unit_conversion_stored(self, basic_normalizer):
        """resolve applies unit conversion and stores scale."""
        resolved = basic_normalizer.resolve(["Voltage-mV"])
        assert resolved["voltage_volt"].scale == pytest.approx(0.001)

    def test_resolve_resolved_column_passthrough(self):
        """resolve passes through ResolvedColumn fields unchanged."""
        rc = ResolvedColumn(source_header="my_col", scale=0.001)
        n = TableNormalizer(voltage_volt=rc)
        resolved = n.resolve(["my_col"])
        assert resolved["voltage_volt"] is rc

    def test_resolve_first_claim_wins(self):
        """resolve assigns each header to first matching field in declaration order."""
        n = TableNormalizer(
            voltage_volt=(Syn("Col-{unit}"),),
            current_ampere=(Syn("Col-{unit}"),),
        )
        resolved = n.resolve(["Col-V"])
        assert "voltage_volt" in resolved
        assert "current_ampere" not in resolved

    def test_resolve_tilde_prefix_stripped(self):
        """resolve strips leading ~ from header during matching, keeps in source_header."""
        n = TableNormalizer(test_time_second=(Syn("Time[s]"),))
        resolved = n.resolve(["~Time[s]"])
        assert "test_time_second" in resolved
        assert resolved["test_time_second"].source_header == "~Time[s]"

    def test_resolve_partial_headers(self, basic_normalizer):
        """resolve works with partial header list."""
        resolved = basic_normalizer.resolve(["Voltage-V"])
        assert "voltage_volt" in resolved
        assert "current_ampere" not in resolved

    def test_resolve_unknown_headers_ignored(self, basic_normalizer):
        """resolve ignores headers that don't match any field."""
        resolved = basic_normalizer.resolve(["Voltage-V", "unknown_col_xyz"])
        assert "voltage_volt" in resolved
        assert len(resolved) == 1

    def test_resolve_empty_headers(self, basic_normalizer):
        """resolve returns empty dict when given empty header list."""
        assert basic_normalizer.resolve([]) == {}

    def test_resolve_multiple_synonyms_fallback(self):
        """resolve tries each synonym in order until one matches."""
        n = TableNormalizer(current_ampere=(Syn("Current-{unit}"), Syn("Amps-{unit}")))
        resolved = n.resolve(["Amps-mA"])
        assert "current_ampere" in resolved
        assert resolved["current_ampere"].scale == pytest.approx(0.001)


class TestNormalizerScore:
    @pytest.fixture
    def normalizer(self):
        return TableNormalizer(
            test_time_second=(DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn("Voltage-{unit}"),),
            current_ampere=(Syn("Current-{unit}"),),
        )

    def test_score_all_match(self, normalizer):
        """score returns count of fields that match headers."""
        assert normalizer.score_columns(["Test-Time", "Voltage-V", "Current-mA"]) == 3

    def test_score_partial_match(self, normalizer):
        """score counts only the matching fields."""
        assert normalizer.score_columns(["Voltage-V"]) == 1

    def test_score_no_match(self, normalizer):
        """score returns 0 when no headers match."""
        assert normalizer.score_columns(["unknown_col"]) == 0

    def test_score_incompatible_unit_reduces_score(self, normalizer):
        """score doesn't count matches with incompatible units."""
        assert normalizer.score_columns(["Voltage-V", "Current-V"]) == 1

    def test_score_extra_irrelevant_columns_ignored(self, normalizer):
        """score ignores extra columns not in the normalizer."""
        score = normalizer.score_columns(["Test-Time", "Voltage-V", "Current-mA", "extra_col"])
        assert score == 3

    def test_score_with_resolved_column(self):
        """score works with ResolvedColumn fields."""
        n = TableNormalizer(voltage_volt=ResolvedColumn(source_header="my_v"))
        assert n.score_columns(["my_v"]) == 1
        assert n.score_columns(["other"]) == 0


class TestKnownHeaderNames:
    def test_resolved_column_only(self):
        """known_header_names returns only ResolvedColumn source_headers, not synonyms."""
        n = TableNormalizer(
            test_time_second=(DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn("Voltage-{unit}"), Syn("U")),
            current_ampere=ResolvedColumn(source_header="my_current"),
        )
        assert n.known_header_names() == ["my_current"]

    def test_multiple_resolved_columns(self):
        """known_header_names lists all ResolvedColumn source_headers in declaration order."""
        n = TableNormalizer(
            test_time_second=ResolvedColumn(source_header="time"),
            voltage_volt=ResolvedColumn(source_header="my_v"),
            current_ampere=ResolvedColumn(source_header="my_i"),
        )
        assert n.known_header_names() == ["time", "my_v", "my_i"]

    def test_empty_normalizer(self):
        """known_header_names returns empty list for normalizer with no ResolvedColumns."""
        assert TableNormalizer().known_header_names() == []

    def test_synonyms_excluded(self):
        """known_header_names excludes synonym fields entirely."""
        n = TableNormalizer(
            voltage_volt=(Syn("Voltage-{unit}"), Syn("U")),
            current_ampere=(Syn("Current-{unit}"),),
        )
        assert n.known_header_names() == []

    def test_mixed_synonyms_and_resolved(self):
        """known_header_names includes only ResolvedColumns, skips synonym fields."""
        n = TableNormalizer(
            test_time_second=(Syn("Test-Time"),),
            voltage_volt=ResolvedColumn(source_header="v_source"),
            current_ampere=(Syn("Current-{unit}"),),
            cycle_count=ResolvedColumn(source_header="cycle_source"),
        )
        assert n.known_header_names() == ["v_source", "cycle_source"]


class TestNormalizerNormalize:
    @pytest.fixture
    def normalizer(self):
        return TableNormalizer(
            test_time_second=(DateTimeSyn(syn=Syn("Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn("Voltage-{unit}"),),
            current_ampere=(Syn("Current-{unit}"),),
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
        """normalize returns DataFrame with BDF canonical column names."""
        out = normalizer.normalize(simple_df)
        assert "Test Time / s" in out.columns
        assert "Voltage / V" in out.columns
        assert "Current / A" in out.columns

    def test_normalize_unit_conversion(self, normalizer, simple_df):
        """normalize applies scale to unit conversions."""
        out = normalizer.normalize(simple_df)
        assert out["Current / A"].to_list() == pytest.approx([0.01, 0.01, 0.01])

    def test_normalize_duration_string_to_seconds(self, normalizer, simple_df):
        """normalize parses duration strings to elapsed seconds."""
        out = normalizer.normalize(simple_df)
        assert out["Test Time / s"].to_list() == pytest.approx([0.0, 1.0, 2.0])

    def test_normalize_dataframe_returns_dataframe(self, normalizer, simple_df):
        """normalize DataFrame returns DataFrame."""
        assert isinstance(normalizer.normalize(simple_df), pl.DataFrame)

    def test_normalize_lazyframe_returns_lazyframe(self, normalizer, simple_df):
        """normalize LazyFrame returns LazyFrame."""
        lf = simple_df.lazy()
        out = normalizer.normalize(lf)
        assert isinstance(out, pl.LazyFrame)
        assert "Voltage / V" in out.collect().columns

    @pytest.mark.filterwarnings("ignore::UserWarning")
    def test_normalize_include_optional_false_excludes_optional(self):
        """normalize with include_optional=False excludes optional columns."""
        n = TableNormalizer(
            test_time_second=(Syn("t"),),
            voltage_volt=(Syn("v"),),
            current_ampere=(Syn("i"),),
            cycle_count=(Syn("cycle"),),
        )
        df = pl.DataFrame({"t": [1.0], "v": [3.5], "i": [0.1], "cycle": [1.0]})
        out = n.normalize(df, include_optional=False)
        assert "Test Time / s" in out.columns
        assert "Cycle Count / 1" not in out.columns

    def test_normalize_no_exprs_returns_df_unchanged(self):
        """normalize returns input unchanged when no columns match."""
        n = TableNormalizer(voltage_volt=(Syn("Voltage-{unit}"),))
        df = pl.DataFrame({"unrelated": [1.0, 2.0]})
        out = n.normalize(df)
        assert out is df

    def test_normalize_extra_columns_passthrough(self, simple_df):
        """normalize includes extra_columns with specified names."""
        n = TableNormalizer(voltage_volt=(Syn("Voltage-{unit}"),))
        out = n.normalize(simple_df, extra_columns={"Test-Time": "raw_time"})
        assert "raw_time" in out.columns
        assert out["raw_time"].to_list() == simple_df["Test-Time"].to_list()

    def test_normalize_extra_columns_missing_warns(self, simple_df):
        """normalize warns when extra_columns references missing source column."""
        n = TableNormalizer(voltage_volt=(Syn("Voltage-{unit}"),))
        with pytest.warns(UserWarning, match="not in DataFrame"):
            n.normalize(simple_df, extra_columns={"ghost_col": "Out"})

    def test_normalize_missing_required_warns(self):
        """normalize warns when required BDF columns are missing."""
        n = TableNormalizer(voltage_volt=(Syn("v"),))
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
        """normalize correctly converts units across different measurement types."""
        field_map = {
            "V": "voltage_volt",
            "A": "current_ampere",
            "s": "test_time_second",
        }
        syn_map: dict[str, tuple[Syn | DateTimeSyn, ...] | ResolvedColumn | None] = {
            "voltage_volt": (Syn("Voltage-{unit}"),),
            "current_ampere": (Syn("Current-{unit}"),),
            "test_time_second": (Syn("Time-{unit}"),),
        }
        mr = field_map[bdf_unit]
        n = TableNormalizer(**{mr: syn_map[mr]})
        df = pl.DataFrame({col: [value]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df)
        assert out[header][0] == expected

    def test_normalize_int_dtype_cycle_count(self):
        """normalize casts cycle_count to Int64."""
        n = TableNormalizer(cycle_count=(Syn("cycle"),))
        df = pl.DataFrame({"cycle": [1.0, 2.0, 3.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df)
        assert out["Cycle Count / 1"].dtype == pl.Int64

    def test_normalize_float_dtype_voltage(self):
        """normalize casts voltage_volt to Float64."""
        n = TableNormalizer(voltage_volt=(Syn("v"),))
        df = pl.DataFrame({"v": [3.5]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df)
        assert out["Voltage / V"].dtype == pl.Float64


class TestNormalizerFromColumnMap:
    def test_empty_dict_raises(self):
        """from_column_map raises ValueError for empty dict."""
        with pytest.raises(ValueError, match="column_map must not be empty"):
            TableNormalizer.from_column_map({})

    def test_single_entry_returns_normalizer(self):
        """from_column_map with one valid entry returns a TableNormalizer."""
        n = TableNormalizer.from_column_map({"Voltage / V": "my_v"})
        assert isinstance(n, TableNormalizer)

    def test_resolved_column_set_on_correct_field(self):
        """from_column_map sets the ResolvedColumn on the correct mr_name field."""
        n = TableNormalizer.from_column_map({"Voltage / V": "my_v"})
        assert isinstance(n.voltage_volt, ResolvedColumn)
        assert n.voltage_volt.source_header == "my_v"

    def test_source_header_preserved(self):
        """from_column_map stores the original source header string."""
        n = TableNormalizer.from_column_map({"Current / A": "raw_current_col"})
        assert isinstance(n.current_ampere, ResolvedColumn)
        assert n.current_ampere.source_header == "raw_current_col"

    def test_same_unit_scale_one(self):
        """from_column_map sets scale=1.0 when key unit matches BDF unit."""
        n = TableNormalizer.from_column_map({"Voltage / V": "v"})
        assert isinstance(n.voltage_volt, ResolvedColumn)
        assert n.voltage_volt.scale == pytest.approx(1.0)

    def test_unit_conversion_millivolt(self):
        """from_column_map sets scale=0.001 when key unit is mV and BDF unit is V."""
        n = TableNormalizer.from_column_map({"Voltage / mV": "v_mv"})
        assert isinstance(n.voltage_volt, ResolvedColumn)
        assert n.voltage_volt.scale == pytest.approx(0.001)
        assert n.voltage_volt.offset == pytest.approx(0.0)

    def test_unit_conversion_milliampere(self):
        """from_column_map sets scale=0.001 when key unit is mA and BDF unit is A."""
        n = TableNormalizer.from_column_map({"Current / mA": "i_ma"})
        assert isinstance(n.current_ampere, ResolvedColumn)
        assert n.current_ampere.scale == pytest.approx(0.001)

    def test_unit_conversion_hours_to_seconds(self):
        """from_column_map converts hours to seconds (scale=3600)."""
        n = TableNormalizer.from_column_map({"Test Time / h": "t_h"})
        assert isinstance(n.test_time_second, ResolvedColumn)
        assert n.test_time_second.scale == pytest.approx(3600.0)

    def test_multiple_entries(self):
        """from_column_map handles multiple entries and sets each field correctly."""
        n = TableNormalizer.from_column_map(
            {
                "Voltage / mV": "col_v",
                "Current / mA": "col_i",
                "Test Time / s": "col_t",
            }
        )
        assert isinstance(n.voltage_volt, ResolvedColumn)
        assert isinstance(n.current_ampere, ResolvedColumn)
        assert isinstance(n.test_time_second, ResolvedColumn)
        assert n.voltage_volt.source_header == "col_v"
        assert n.current_ampere.source_header == "col_i"
        assert n.test_time_second.source_header == "col_t"

    def test_unset_fields_remain_none(self):
        """from_column_map leaves unspecified fields as None."""
        n = TableNormalizer.from_column_map({"Voltage / V": "v"})
        assert n.current_ampere is None
        assert n.test_time_second is None

    def test_invalid_label_raises(self):
        """from_column_map raises ValueError for unknown BDF label base."""
        with pytest.raises(ValueError, match="label base not found"):
            TableNormalizer.from_column_map({"NotReal / V": "col"})

    def test_incompatible_unit_warns_and_uses_scale_one(self):
        """from_column_map warns on incompatible unit and falls back to scale=1.0."""
        with pytest.warns(UserWarning, match="not compatible"):
            n = TableNormalizer.from_column_map({"Voltage / A": "col_v"})
        assert isinstance(n.voltage_volt, ResolvedColumn)
        assert n.voltage_volt.scale == pytest.approx(1.0)

    def test_fields_are_resolved_column_not_syn_list(self):
        """from_column_map produces ResolvedColumn fields, not synonym lists."""
        n = TableNormalizer.from_column_map({"Current / A": "i"})
        assert isinstance(n.current_ampere, ResolvedColumn)

    def test_can_normalize_dataframe(self):
        """TableNormalizer built from from_column_map correctly normalizes a DataFrame."""
        n = TableNormalizer.from_column_map(
            {
                "Voltage / mV": "v_mv",
                "Current / mA": "i_ma",
            }
        )
        df = pl.DataFrame({"v_mv": [1000.0, 2000.0], "i_ma": [500.0, 1000.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df)
        assert "Voltage / V" in out.columns
        assert "Current / A" in out.columns
        assert out["Voltage / V"].to_list() == pytest.approx([1.0, 2.0])
        assert out["Current / A"].to_list() == pytest.approx([0.5, 1.0])

    def test_resolve_uses_source_header(self):
        """TableNormalizer from from_column_map resolves to correct source_header in resolve()."""
        n = TableNormalizer.from_column_map({"Voltage / V": "vendor_v"})
        resolved = n.resolve(["vendor_v"])
        assert "voltage_volt" in resolved
        assert resolved["voltage_volt"].source_header == "vendor_v"

    def test_duplicate_mr_name_last_wins(self):
        """from_column_map with two keys mapping to same mr_name: last entry wins."""
        n = TableNormalizer.from_column_map(
            {
                "Voltage / V": "first_col",
                "Voltage / mV": "second_col",
            }
        )
        assert isinstance(n.voltage_volt, ResolvedColumn)
        assert n.voltage_volt.source_header == "second_col"
        assert n.voltage_volt.scale == pytest.approx(0.001)


class TestNormalizerModelValidate:
    def test_json_validation_synonym_list(self):
        """model_validate accepts dict with Syn and DateTimeSyn data."""
        n = TableNormalizer.model_validate(
            {
                "test_time_second": [{"syn": "Test-Time", "fmts": ["%H:%M:%S.%f"]}],
                "voltage_volt": ["Voltage-{unit}"],
                "current_ampere": ["Current-{unit}"],
            }
        )
        assert n.score_columns(["Test-Time", "Voltage-V", "Current-mA"]) == 3

    def test_json_validation_resolved_column(self):
        """model_validate accepts dict with ResolvedColumn data."""
        n = TableNormalizer.model_validate(
            {
                "voltage_volt": {"source_header": "my_v", "scale": 0.001},
            }
        )
        resolved = n.resolve(["my_v"])
        assert resolved["voltage_volt"].scale == pytest.approx(0.001)


# Top-level normalize() function


class TestNormalizeFn:
    def test_no_source_no_normalizer_no_extra_returns_df(self):
        """normalize() returns input unchanged when no normalization applies."""
        df = pl.DataFrame({"unknown_col": [1.0, 2.0]})
        out = normalize(df)
        assert out is df

    def test_normalizer_only_no_source(self):
        """normalize() with explicit normalizer bypasses source detection."""
        df = pl.DataFrame({"my_v": [1000.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(df, normalizer={"Voltage / mV": "my_v"})
        assert "Voltage / V" in out.columns
        assert out["Voltage / V"][0] == pytest.approx(1.0)

    def test_extra_columns_only_no_source(self):
        """normalize() with extra_columns passes through extra columns."""
        df = pl.DataFrame({"raw": [1.0, 2.0]})
        out = normalize(df, extra_columns={"raw": "Raw Out"})
        assert "Raw Out" in out.columns

    def test_lazyframe_passthrough_unchanged(self):
        """normalize() on unknown LazyFrame returns it unchanged."""
        lf = pl.LazyFrame({"unknown_xyz": [1.0]})
        out = normalize(lf)
        assert isinstance(out, pl.LazyFrame)
        assert out is lf

    def test_explicit_normalizer_uses_its_mapping(self):
        """normalize() with an explicit normalizer uses that normalizer's mapping."""
        norm = TableNormalizer(voltage_volt=(Syn("v"),))
        df = pl.DataFrame({"v": [3.5]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(df, normalizer=norm)
        assert "Voltage / V" in out.columns
