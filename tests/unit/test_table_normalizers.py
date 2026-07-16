"""Unit tests for src/bdf/table_normalizers.py."""

from __future__ import annotations

import warnings
from typing import cast

import polars as pl
import pytest
from pydantic import ValidationError

from bdf.spec import COLUMN_ONTOLOGY
from bdf.table_normalizers import (
    BDF_NORMALIZER,
    NORMALIZERS,
    DateTimeSyn,
    ResolvedColumn,
    Syn,
    TableNormalizer,
    normalize,
)


class TestSyn:
    def test_exemplar_property(self):
        """Exemplar property returns the root synonym pattern."""
        assert Syn(hdr="Voltage-{unit}").hdr == "Voltage-{unit}"

    @pytest.mark.parametrize(
        "header,expected",
        [
            ("test-time", True),
            ("Test-Time", False),
            ("TEST-TIME", False),
            ("  test-time  ", True),
            ("other", False),
        ],
    )
    def test_exact_match_case_sensitive(self, header, expected):
        """exact_match is case-sensitive and ignores surrounding whitespace."""
        assert Syn(hdr="test-time").exact_match(header) is expected

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
            result = Syn(hdr="Voltage-{unit}").match(header, bdf_unit)
        elif "Current" in header:
            result = Syn(hdr="Current-{unit}").match(header, bdf_unit)
        elif "Time" in header:
            result = Syn(hdr="Time-{unit}").match(header, bdf_unit)
        else:
            result = Syn(hdr="Pressure-{unit}").match(header, bdf_unit)
        assert result is not None
        scale, offset = result
        assert scale == expected_scale
        assert offset == expected_offset

    def test_match_with_unit_returns_none_incompatible(self):
        """match returns None when header's dimension is incompatible with bdf_unit."""
        assert Syn(hdr="Voltage-{unit}").match("Voltage-V", "A") is None

    def test_match_with_unit_returns_none_wrong_base(self):
        """match returns None when header base doesn't match synonym."""
        assert Syn(hdr="Voltage-{unit}").match("Current-A", "V") is None

    def test_match_no_unit_exact(self):
        """match on pattern without {unit} returns (1.0, 0.0) for exact case-insensitive match."""
        result = Syn(hdr="Test-Time").match("Test-Time", "s")
        assert result == (1.0, 0.0)

    def test_match_no_unit_case_sensitive(self):
        """match without {unit} placeholder is case-sensitive."""
        assert Syn(hdr="test-time").match("test-time", "s") == (1.0, 0.0)
        assert Syn(hdr="test-time").match("Test-Time", "s") is None

    def test_match_no_unit_mismatch(self):
        """match without {unit} returns None when header doesn't match."""
        assert Syn(hdr="Test-Time").match("Other", "s") is None

    def test_match_exact_syn_with_none_unit(self):
        """Syn without {unit} matches against unit=None column → (1.0, 0.0)."""
        assert Syn(hdr="Step ID").match("Step ID", None) == (1.0, 0.0)

    def test_match_unit_parameterised_syn_with_none_unit(self):
        """Syn with {unit} against unit=None column → None."""
        assert Syn(hdr="Step/{unit}").match("Step/s", None) is None

    def test_model_validate_string(self):
        """model_validate coerces a plain string argument to a Syn instance."""
        s = Syn.model_validate("Voltage-{unit}")
        assert s.hdr == "Voltage-{unit}"

    def test_assumed_defaults_false(self):
        """assumed defaults to False when not specified."""
        assert Syn(hdr="x").assumed is False

    def test_assumed_explicit_true(self):
        """assumed can be set to True at construction."""
        assert Syn(hdr="x", assumed=True).assumed is True

    def test_frozen(self):
        """Syn is frozen and cannot be mutated after creation."""
        s = Syn(hdr="x")
        with pytest.raises(ValidationError):
            s.hdr = "y"


class TestDateTimeSyn:
    def test_construction(self):
        """DateTimeSyn stores syn and fmts during construction."""
        dts = DateTimeSyn(syn=Syn(hdr="Test-Time"), fmts=("%H:%M:%S.%f",))
        assert dts.syn.hdr == "Test-Time"
        assert dts.fmts == ("%H:%M:%S.%f",)

    def test_fmts_stored_as_tuple(self):
        """DateTimeSyn converts fmts list to tuple."""
        dts = DateTimeSyn(syn=Syn(hdr="T"), fmts=("%H:%M:%S", "%Y-%m-%d"))
        assert isinstance(dts.fmts, tuple)
        assert len(dts.fmts) == 2

    def test_model_validate_dict(self):
        """model_validate accepts dict with string syn and list fmts."""
        dts = DateTimeSyn.model_validate({"syn": "Test-Time", "fmts": ["%H:%M:%S.%f"]})
        assert dts.syn.hdr == "Test-Time"
        assert "%H:%M:%S.%f" in dts.fmts

    def test_frozen(self):
        """DateTimeSyn is frozen and cannot be mutated after creation."""
        dts = DateTimeSyn(syn=Syn(hdr="T"), fmts=("%H:%M:%S",))
        with pytest.raises(ValidationError):
            dts.fmts = ("%Y",)


class TestResolvedColumn:
    # --- from_bdf_label ---

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
    def test_from_bdf_label_unit_conversion(self, bdf_label, src_col, expected_mr, expected_scale):
        """from_bdf_label converts BDF label to mr_name and applies unit scaling."""
        mr, rc = ResolvedColumn.from_bdf_label(bdf_label, src_col)
        assert mr == expected_mr
        assert rc.source_header == src_col
        assert rc.scale == expected_scale
        assert rc.offset == pytest.approx(0.0)

    def test_from_bdf_label_invalid_label_raises(self):
        """from_bdf_label raises ValueError for unknown BDF label."""
        with pytest.raises(ValueError, match="label base not found"):
            ResolvedColumn.from_bdf_label("NotReal / V", "col")

    def test_from_bdf_label_incompatible_unit_warns(self):
        """from_bdf_label warns on incompatible unit and uses scale 1.0."""
        with pytest.warns(UserWarning, match="not compatible"):
            mr, rc = ResolvedColumn.from_bdf_label("Voltage / A", "col_v")
        assert rc.scale == 1.0

    # --- from_synonyms ---

    def test_from_synonyms_matches_syn(self):
        """from_synonyms matches Syn and returns ResolvedColumn with scale conversion."""
        syns = [Syn(hdr="Voltage-{unit}")]
        rc = ResolvedColumn.from_synonyms("Voltage-mV", "Voltage-mV", "V", syns)
        assert rc is not None
        assert rc.source_header == "Voltage-mV"
        assert rc.scale == pytest.approx(0.001)

    def test_from_synonyms_matches_datetimesyn(self):
        """from_synonyms matches DateTimeSyn and stores format strings."""
        syns = [DateTimeSyn(syn=Syn(hdr="Test-Time"), fmts=("%H:%M:%S.%f",))]
        rc = ResolvedColumn.from_synonyms("Test-Time", "Test-Time", "s", syns)
        assert rc is not None
        assert rc.source_header == "Test-Time"
        assert "%H:%M:%S.%f" in rc.datetime_fmts

    def test_from_synonyms_no_match_returns_none(self):
        """from_synonyms returns None when no synonym matches."""
        syns = [Syn(hdr="Voltage-{unit}")]
        assert ResolvedColumn.from_synonyms("Unknown", "Unknown", "V", syns) is None

    def test_from_synonyms_first_match_wins(self):
        """from_synonyms returns first matching synonym, stops checking."""
        syns = [Syn(hdr="Col-{unit}"), Syn(hdr="Col-mV")]
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


def test_normalizer_is_hashable() -> None:
    """A TableNormalizer with synonym fields is hashable and can live in a frozenset."""
    n = TableNormalizer(voltage_volt=(Syn(hdr="voltage"),), current_ampere=(Syn(hdr="current"),))
    assert n in frozenset({n})


def test_normalizer_synonym_field_is_tuple() -> None:
    """Tuple input is stored as a tuple (order preserved)."""
    n = TableNormalizer(voltage_volt=(Syn(hdr="a"), Syn(hdr="b")))
    assert isinstance(n.voltage_volt, tuple)
    assert [cast(Syn, s).hdr for s in n.voltage_volt] == ["a", "b"]


class TestNormalizerIter:
    def test_iter_yields_only_non_none(self):
        """__iter__ yields only non-None fields."""
        n = TableNormalizer(voltage_volt=(Syn(hdr="Voltage-{unit}"),))
        items = list(n)
        assert len(items) == 1
        assert items[0][0] == "voltage_volt"

    def test_iter_declaration_order(self):
        """__iter__ yields fields in declaration order."""
        n = TableNormalizer(
            voltage_volt=(Syn(hdr="Voltage-{unit}"),),
            current_ampere=(Syn(hdr="Current-{unit}"),),
            test_time_second=(DateTimeSyn(syn=Syn(hdr="T"), fmts=("%H:%M:%S",)),),
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
            test_time_second=(DateTimeSyn(syn=Syn(hdr="Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn(hdr="Voltage-{unit}"),),
            current_ampere=(Syn(hdr="Current-{unit}"),),
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
            voltage_volt=(Syn(hdr="Col-{unit}"),),
            current_ampere=(Syn(hdr="Col-{unit}"),),
        )
        resolved = n.resolve(["Col-V"])
        assert "voltage_volt" in resolved
        assert "current_ampere" not in resolved

    def test_resolve_tilde_prefix_stripped(self):
        """resolve strips leading ~ from header during matching, keeps in source_header."""
        n = TableNormalizer(test_time_second=(Syn(hdr="Time[s]"),))
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
        n = TableNormalizer(current_ampere=(Syn(hdr="Current-{unit}"), Syn(hdr="Amps-{unit}")))
        resolved = n.resolve(["Amps-mA"])
        assert "current_ampere" in resolved
        assert resolved["current_ampere"].scale == pytest.approx(0.001)


class TestNormalizerScore:
    @pytest.fixture
    def normalizer(self):
        return TableNormalizer(
            test_time_second=(DateTimeSyn(syn=Syn(hdr="Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn(hdr="Voltage-{unit}"),),
            current_ampere=(Syn(hdr="Current-{unit}"),),
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
            test_time_second=(DateTimeSyn(syn=Syn(hdr="Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn(hdr="Voltage-{unit}"), Syn(hdr="U")),
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
            voltage_volt=(Syn(hdr="Voltage-{unit}"), Syn(hdr="U")),
            current_ampere=(Syn(hdr="Current-{unit}"),),
        )
        assert n.known_header_names() == []

    def test_mixed_synonyms_and_resolved(self):
        """known_header_names includes only ResolvedColumns, skips synonym fields."""
        n = TableNormalizer(
            test_time_second=(Syn(hdr="Test-Time"),),
            voltage_volt=ResolvedColumn(source_header="v_source"),
            current_ampere=(Syn(hdr="Current-{unit}"),),
            cycle_count=ResolvedColumn(source_header="cycle_source"),
        )
        assert n.known_header_names() == ["v_source", "cycle_source"]


class TestNormalizerNormalize:
    @pytest.fixture
    def normalizer(self):
        return TableNormalizer(
            test_time_second=(DateTimeSyn(syn=Syn(hdr="Test-Time"), fmts=("%H:%M:%S.%f",)),),
            voltage_volt=(Syn(hdr="Voltage-{unit}"),),
            current_ampere=(Syn(hdr="Current-{unit}"),),
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

    def test_normalize_pandas_dataframe_returns_pandas_dataframe(self, normalizer, simple_df):
        """normalize pandas DataFrame returns pandas DataFrame with BDF columns."""
        import pandas as pd

        pdf = simple_df.to_pandas()
        out = normalizer.normalize(pdf)  # type: ignore[arg-type]
        assert isinstance(out, pd.DataFrame)
        assert "Voltage / V" in out.columns
        assert "Current / A" in out.columns

    @pytest.mark.filterwarnings("ignore::UserWarning")
    def test_normalize_include_optional_false_excludes_optional(self):
        """normalize with include_optional=False excludes optional columns."""
        n = TableNormalizer(
            test_time_second=(Syn(hdr="t"),),
            voltage_volt=(Syn(hdr="v"),),
            current_ampere=(Syn(hdr="i"),),
            cycle_count=(Syn(hdr="cycle"),),
        )
        df = pl.DataFrame({"t": [1.0], "v": [3.5], "i": [0.1], "cycle": [1.0]})
        out = n.normalize(df, include_optional=False)
        assert "Test Time / s" in out.columns
        assert "Cycle Count / 1" not in out.columns

    def test_normalize_no_exprs_returns_df_unchanged(self):
        """normalize(validate=False) returns equivalent DataFrame when no columns match."""
        n = TableNormalizer(voltage_volt=(Syn(hdr="Voltage-{unit}"),))
        df = pl.DataFrame({"unrelated": [1.0, 2.0]})
        out = n.normalize(df, validate=False)
        assert isinstance(out, pl.DataFrame)
        assert out.equals(df)

    def test_normalize_extra_columns_passthrough(self, simple_df):
        """normalize includes extra_columns with specified names."""
        n = TableNormalizer(voltage_volt=(Syn(hdr="Voltage-{unit}"),))
        out = n.normalize(simple_df, extra_columns={"Test-Time": "raw_time"}, validate=False)
        assert "raw_time" in out.columns
        assert out["raw_time"].to_list() == simple_df["Test-Time"].to_list()

    def test_normalize_extra_columns_missing_warns(self, simple_df):
        """normalize warns when extra_columns references missing source column."""
        n = TableNormalizer(voltage_volt=(Syn(hdr="Voltage-{unit}"),))
        with pytest.warns(UserWarning, match="not in DataFrame"):
            n.normalize(simple_df, extra_columns={"ghost_col": "Out"}, validate=False)

    def test_normalize_missing_required_warns(self):
        """normalize(validate=False) warns when required BDF columns are missing."""
        n = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        df = pl.DataFrame({"v": [3.5]})
        with pytest.warns(UserWarning, match="required BDF columns missing"):
            n.normalize(df, validate=False)

    def test_normalize_missing_required_validate_true_raises(self):
        """normalize(validate=True) raises BDFValidationError when required BDF columns are missing."""
        from bdf.validate import BDFValidationError

        n = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        df = pl.DataFrame({"v": [3.5]})
        with pytest.raises(BDFValidationError, match="Missing required BDF columns"):
            n.normalize(df, validate=True)

    def test_normalize_validate_true_passes_for_valid_frame(self, normalizer, simple_df):
        """normalize(validate=True) does not raise when all required BDF columns are present."""
        out = normalizer.normalize(simple_df, validate=True)
        assert "Voltage / V" in out.columns

    def test_normalize_validate_true_does_not_also_warn(self, recwarn):
        """normalize(validate=True) raises without also emitting the soft missing-columns warning."""
        from bdf.validate import BDFValidationError

        n = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        df = pl.DataFrame({"v": [3.5]})
        with pytest.raises(BDFValidationError):
            n.normalize(df, validate=True)
        assert not any("required BDF columns missing" in str(w.message) for w in recwarn.list)

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
            "voltage_volt": (Syn(hdr="Voltage-{unit}"),),
            "current_ampere": (Syn(hdr="Current-{unit}"),),
            "test_time_second": (Syn(hdr="Time-{unit}"),),
        }
        mr = field_map[bdf_unit]
        n = TableNormalizer(**{mr: syn_map[mr]})
        df = pl.DataFrame({col: [value]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df, validate=False)
        assert out[header][0] == expected

    def test_normalize_int_dtype_cycle_count(self):
        """normalize casts cycle_count to Int64."""
        n = TableNormalizer(cycle_count=(Syn(hdr="cycle"),))
        df = pl.DataFrame({"cycle": [1.0, 2.0, 3.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df, validate=False)
        assert out["Cycle Count / 1"].dtype == pl.Int64

    def test_normalize_float_dtype_voltage(self):
        """normalize casts voltage_volt to Float64."""
        n = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        df = pl.DataFrame({"v": [3.5]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df, validate=False)
        assert out["Voltage / V"].dtype == pl.Float64

    def test_normalize_step_type_produces_utf8(self):
        """normalize step_type column yields Utf8 output column 'Step Type'."""
        n = TableNormalizer(step_type=(Syn(hdr="step_type"),))
        df = pl.DataFrame({"step_type": ["CC_CHG", "CC_DCH", "REST"]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df, validate=False)
        assert "Step Type" in out.columns
        assert out["Step Type"].dtype == pl.Utf8

    def test_normalize_step_id_produces_int64(self):
        """normalize step_id column yields Int64 output column 'Step ID'."""
        n = TableNormalizer(step_id=(Syn(hdr="step_id"),))
        df = pl.DataFrame({"step_id": [1.0, 2.0, 3.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df, validate=False)
        assert "Step ID" in out.columns
        assert out["Step ID"].dtype == pl.Int64


class TestNormalizerFromColumnMap:
    """Unit conversion math itself is covered by TestResolvedColumn.from_bdf_label;
    from_column_map is a thin per-key wrapper around it, so these tests focus on the
    wrapper's own behavior (multi-entry assembly, unset fields, duplicate keys)."""

    def test_empty_dict_raises(self):
        """from_column_map raises ValueError for empty dict."""
        with pytest.raises(ValueError, match="column_map must not be empty"):
            TableNormalizer.from_column_map({})

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

    def test_multiple_entries(self):
        """from_column_map builds a ResolvedColumn per entry with source header and unit scale set."""
        n = TableNormalizer.from_column_map(
            {
                "Voltage / mV": "col_v",
                "Current / mA": "col_i",
                "Test Time / h": "col_t",
            }
        )
        assert isinstance(n.voltage_volt, ResolvedColumn)
        assert isinstance(n.current_ampere, ResolvedColumn)
        assert isinstance(n.test_time_second, ResolvedColumn)
        assert n.voltage_volt.source_header == "col_v"
        assert n.current_ampere.source_header == "col_i"
        assert n.test_time_second.source_header == "col_t"
        assert n.voltage_volt.scale == pytest.approx(0.001)
        assert n.current_ampere.scale == pytest.approx(0.001)
        assert n.test_time_second.scale == pytest.approx(3600.0)
        assert n.cycle_count is None

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

    def test_can_normalize_dataframe(self):
        """TableNormalizer built from from_column_map resolves and normalizes a DataFrame end-to-end."""
        n = TableNormalizer.from_column_map(
            {
                "Voltage / mV": "v_mv",
                "Current / mA": "i_ma",
            }
        )
        resolved = n.resolve(["v_mv", "i_ma"])
        assert resolved["voltage_volt"].source_header == "v_mv"

        df = pl.DataFrame({"v_mv": [1000.0, 2000.0], "i_ma": [500.0, 1000.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = n.normalize(df, validate=False)
        assert "Voltage / V" in out.columns
        assert "Current / A" in out.columns
        assert out["Voltage / V"].to_list() == pytest.approx([1.0, 2.0])
        assert out["Current / A"].to_list() == pytest.approx([0.5, 1.0])


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


class TestNormalizeFn:
    def test_no_source_no_normalizer_no_extra_returns_df(self):
        """normalize(validate=False) returns input unchanged when no normalization applies."""
        df = pl.DataFrame({"unknown_col": [1.0, 2.0]})
        out = normalize(df, validate=False)
        assert out is df

    def test_normalizer_only_no_source(self):
        """normalize() with explicit normalizer bypasses source detection."""
        df = pl.DataFrame({"my_v": [1000.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(df, normalizer={"Voltage / mV": "my_v"}, validate=False)
        assert "Voltage / V" in out.columns
        assert out["Voltage / V"][0] == pytest.approx(1.0)

    def test_extra_columns_only_no_source(self):
        """normalize() with extra_columns passes through extra columns."""
        df = pl.DataFrame({"raw": [1.0, 2.0]})
        out = normalize(df, extra_columns={"raw": "Raw Out"}, validate=False)
        assert "Raw Out" in out.columns

    def test_lazyframe_passthrough_unchanged(self):
        """normalize(validate=False) on unknown LazyFrame returns it unchanged."""
        lf = pl.LazyFrame({"unknown_xyz": [1.0]})
        out = normalize(lf, validate=False)
        assert isinstance(out, pl.LazyFrame)
        assert out is lf

    def test_validate_true_raises_when_no_normalizer_detected(self):
        """normalize(validate=True) raises BDFValidationError even when no normalizer auto-detects."""
        from bdf.validate import BDFValidationError

        df = pl.DataFrame({"unknown_col": [1.0, 2.0]})
        with pytest.raises(BDFValidationError, match="Missing required BDF columns"):
            normalize(df, validate=True)

    def test_validate_true_passes_when_headers_are_already_canonical(self):
        """normalize(validate=True) passes when df already has all required BDF column names."""
        from bdf.spec import COLUMN_ONTOLOGY

        cols = {label: [1.0] for label in COLUMN_ONTOLOGY.required_labels()}
        df = pl.DataFrame(cols)
        out = normalize(df, validate=True)
        for label in COLUMN_ONTOLOGY.required_labels():
            assert label in out.columns

    def test_explicit_normalizer_uses_its_mapping(self):
        """normalize() with an explicit normalizer uses that normalizer's mapping."""
        norm = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        df = pl.DataFrame({"v": [3.5]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(df, normalizer=norm, validate=False)
        assert "Voltage / V" in out.columns

    def test_normalize_pandas_dataframe_returns_pandas_dataframe(self):
        """normalize() round-trips a pandas DataFrame."""
        import pandas as pd

        norm = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        pdf = pd.DataFrame({"v": [3.5, 4.0]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(pdf, normalizer=norm, validate=False)
        assert isinstance(out, pd.DataFrame)
        assert "Voltage / V" in out.columns

    def test_normalize_polars_dataframe_returns_polars_dataframe(self):
        """normalize() round-trips a polars DataFrame."""
        norm = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        df = pl.DataFrame({"v": [3.5]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(df, normalizer=norm, validate=False)
        assert isinstance(out, pl.DataFrame)

    def test_normalize_polars_lazyframe_returns_polars_lazyframe(self):
        """normalize() round-trips a polars LazyFrame."""
        norm = TableNormalizer(voltage_volt=(Syn(hdr="v"),))
        lf = pl.LazyFrame({"v": [3.5]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = normalize(lf, normalizer=norm, validate=False)
        assert isinstance(out, pl.LazyFrame)


class TestTableNormalizerFieldSync:
    def test_fields_match_non_deprecated_ontology(self):
        """TableNormalizer fields must mirror the non-deprecated ontology quantities.

        Guards against drift: a quantity added to (or removed from) the ontology
        without a matching field edit here will fail this test. Fields are hand-
        declared for static typing on the vendor normalizers; this keeps them honest.
        """
        declared = set(TableNormalizer.model_fields)
        ontology_non_deprecated = {mr_name for mr_name, q in COLUMN_ONTOLOGY if not q.deprecated}
        missing = ontology_non_deprecated - declared
        extra = declared - ontology_non_deprecated
        assert not missing, f"ontology quantities with no TableNormalizer field: {sorted(missing)}"
        assert not extra, f"TableNormalizer fields absent from (non-deprecated) ontology: {sorted(extra)}"


class TestBDFNormalizer:
    def test_all_non_deprecated_mr_names_present(self):
        bdf_fields = {mr_name for mr_name, _ in BDF_NORMALIZER}
        known_fields = set(TableNormalizer.model_fields)
        ontology_non_deprecated = {mr_name for mr_name, q in COLUMN_ONTOLOGY if not q.deprecated}
        expected = ontology_non_deprecated & known_fields
        assert expected == bdf_fields

    def test_bdf_normalizer_scores_highest_on_bdf_headers(self):
        bdf_headers = [
            "Test Time / s",
            "Voltage / V",
            "Current / A",
            "Cycle Count / 1",
        ]
        bdf_score = BDF_NORMALIZER.score_columns(bdf_headers)
        for name, norm in [
            ("arbin", NORMALIZERS["arbin"]),
            ("neware", NORMALIZERS["neware"]),
            ("biologic", NORMALIZERS["biologic"]),
        ]:
            vendor_score = norm.score_columns(bdf_headers)
            assert bdf_score > vendor_score, f"BDF_NORMALIZER should outscore {name}"

    def test_bdf_normalizer_accepts_machine_notation_headers(self):
        """BDF_NORMALIZER maps current on-disk notation headers to preferred labels."""
        lf = pl.DataFrame(
            {
                "test_time_second": [0.0],
                "voltage_volt": [3.7],
                "current_ampere": [0.1],
            }
        ).lazy()
        out = BDF_NORMALIZER.normalize(lf, validate=True).collect()
        assert out.columns == ["Test Time / s", "Voltage / V", "Current / A"]

    def test_bdf_normalizer_converts_deprecated_machine_notation_headers(self):
        """BDF_NORMALIZER converts deprecated notation units to preferred labels."""
        lf = pl.DataFrame(
            {
                "test_time_millisecond": [1500.0],
                "voltage_volt": [3.7],
                "current_ampere": [0.1],
            }
        ).lazy()
        out = BDF_NORMALIZER.normalize(lf, validate=True).collect()
        assert out["Test Time / s"][0] == pytest.approx(1.5)
        assert out.columns == ["Test Time / s", "Voltage / V", "Current / A"]


class TestTimezoneHandling:
    """Naive vs tz-aware unix_time_second parsing under the tz parameter."""

    def test_naive_format_localized_to_explicit_tz(self):
        """Explicit non-UTC tz shifts unix_time_second by the correct offset vs UTC."""
        n = TableNormalizer(unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S",)),))
        df = pl.DataFrame({"ts": ["2024-06-01 12:00:00"]})
        out_utc = n.normalize(df, validate=False, tz="UTC")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out_ny = n.normalize(df, validate=False, tz="America/New_York")
        t_utc = out_utc["Unix Time / s"][0]
        t_ny = out_ny["Unix Time / s"][0]
        # America/New_York is UTC-4 in June (EDT): same wall clock parses to a later instant.
        assert t_ny - t_utc == pytest.approx(4 * 3600.0)

    def test_tz_aware_format_ignores_tz_argument(self):
        """A format with an embedded offset (%:z) produces identical output regardless of tz."""
        n = TableNormalizer(
            unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S%:z",)),),
        )
        df = pl.DataFrame({"ts": ["2024-06-01 12:00:00+02:00"]})
        out_utc = n.normalize(df, validate=False, tz="UTC")
        out_tokyo = n.normalize(df, validate=False, tz="Asia/Tokyo")
        assert out_utc["Unix Time / s"][0] == pytest.approx(out_tokyo["Unix Time / s"][0])

    def test_default_tz_warns_and_matches_legacy_utc_behavior(self):
        """Default call (no tz) on a naive format warns and matches pre-change (UTC) output."""
        n = TableNormalizer(unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S",)),))
        df = pl.DataFrame({"ts": ["2024-06-01 12:00:00"]})
        with pytest.warns(UserWarning, match="tz defaulted to UTC"):
            out = n.normalize(df, validate=False)
        assert out["Unix Time / s"][0] == pytest.approx(1717243200.0)

    def test_tz_aware_only_format_emits_no_warning(self, recwarn):
        """An entirely offset-qualified format emits no timezone warning under default tz."""
        n = TableNormalizer(
            unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S%:z",)),),
        )
        df = pl.DataFrame({"ts": ["2024-06-01 12:00:00+02:00"]})
        n.normalize(df, validate=False)
        assert not any("tz defaulted to UTC" in str(w.message) for w in recwarn.list)

    def test_invalid_tz_raises_before_collect(self):
        """An invalid tz raises ValueError immediately from normalize(), before any lazy collect."""
        n = TableNormalizer(unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S",)),))
        lf = pl.LazyFrame({"ts": ["2024-06-01 12:00:00"]})
        with pytest.raises(ValueError, match="invalid tz 'Not/AZone'.*time zone"):
            n.normalize(lf, validate=False, tz="Not/AZone")

    def test_dst_non_existent_time_becomes_null(self):
        """Spring-forward gap timestamps become null instead of failing the read."""
        n = TableNormalizer(unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S",)),))
        df = pl.DataFrame({"ts": ["2024-03-10 02:30:00"]})
        out = n.normalize(df, validate=False, tz="America/New_York")
        assert out["Unix Time / s"].to_list() == [None]

    def test_dst_ambiguous_time_uses_earliest_occurrence(self):
        """Fall-back repeated hour timestamps choose the earliest occurrence."""
        n = TableNormalizer(unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S",)),))
        df = pl.DataFrame({"ts": ["2024-11-03 01:30:00"]})
        out = n.normalize(df, validate=False, tz="America/New_York")
        assert out["Unix Time / s"][0] == pytest.approx(1730611800.0)

    def test_dst_transition_times_do_not_fail_lazy_collect(self):
        """DST edge cases are handled inside the lazy plan before collect()."""
        n = TableNormalizer(unix_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S",)),))
        lf = pl.LazyFrame({"ts": ["2024-03-10 02:30:00", "2024-11-03 01:30:00"]})
        out = n.normalize(lf, validate=False, tz="America/New_York").collect()
        assert out["Unix Time / s"].to_list() == [None, 1730611800.0]

    def test_elapsed_seconds_identical_regardless_of_tz(self):
        """test_time_second/step_time_second values are unaffected by tz (offset cancels out)."""
        n = TableNormalizer(
            test_time_second=(DateTimeSyn(syn=Syn(hdr="ts"), fmts=("%Y-%m-%d %H:%M:%S",)),),
        )
        df = pl.DataFrame({"ts": ["2024-06-01 12:00:00", "2024-06-01 12:01:00"]})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out_utc = n.normalize(df, validate=False, tz="UTC")
            out_tokyo = n.normalize(df, validate=False, tz="Asia/Tokyo")
        assert out_utc["Test Time / s"].to_list() == pytest.approx(out_tokyo["Test Time / s"].to_list())


class TestExtend:
    def test_append_to_existing_tuple(self):
        """extend() appends new synonyms after the existing ones, built-ins first."""
        norm = TableNormalizer(voltage_volt=(Syn(hdr="Voltage ({unit})"),))
        extended = norm.extend(voltage_volt=(Syn(hdr="U ({unit})"),))
        assert extended.voltage_volt == (Syn(hdr="Voltage ({unit})"), Syn(hdr="U ({unit})"))
        # original is unmodified (frozen)
        assert norm.voltage_volt == (Syn(hdr="Voltage ({unit})"),)

    def test_set_on_none_field(self):
        """extend() sets the value directly when the field was previously unset."""
        norm = TableNormalizer()
        extended = norm.extend(voltage_volt=(Syn(hdr="U ({unit})"),))
        assert extended.voltage_volt == (Syn(hdr="U ({unit})"),)

    def test_single_syn_value_is_wrapped_in_tuple(self):
        """extend() accepts a bare Syn/DateTimeSyn, not just a tuple."""
        norm = TableNormalizer(voltage_volt=(Syn(hdr="Voltage ({unit})"),))
        extended = norm.extend(voltage_volt=Syn(hdr="U ({unit})"))
        assert extended.voltage_volt == (Syn(hdr="Voltage ({unit})"), Syn(hdr="U ({unit})"))

    def test_replace_resolved_column_warns(self):
        """extend() replaces (rather than appends to) a ResolvedColumn field, with a warning."""
        norm = TableNormalizer(voltage_volt=ResolvedColumn(source_header="V"))
        with pytest.warns(UserWarning, match="replacing ResolvedColumn"):
            extended = norm.extend(voltage_volt=(Syn(hdr="U ({unit})"),))
        assert extended.voltage_volt == (Syn(hdr="U ({unit})"),)

    def test_invalid_field_raises(self):
        """extend() raises ValueError for a kwarg that isn't a TableNormalizer field."""
        norm = TableNormalizer()
        with pytest.raises(ValueError, match="unknown TableNormalizer field"):
            norm.extend(not_a_real_field=(Syn(hdr="x"),))


class TestPybammNormalizer:
    """Conversion test on a synthetic PyBaMM-shaped dataframe.

    PyBaMM is consumed in-memory as a dataframe (not a file format), so there is
    no ``Plugin``/file-detection entry — just the ``NORMALIZERS["pybamm"]``
    mapping exercised directly. The frame below uses PyBaMM's native variable
    names (e.g. ``"Voltage [V]"``) as headers and follows its discharge-positive
    sign convention: a discharge half (positive current, rising discharge
    capacity) followed by a charge half (negative current, falling discharge
    capacity), so the charge-positive sign flips are observable.
    """

    @pytest.fixture
    def mock_df(self):
        return pl.DataFrame(
            {
                "Time [s]": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                "Current [A]": [1.0, 1.0, 1.0, -0.5, -0.5, -0.5],
                "Voltage [V]": [4.10, 3.90, 3.70, 3.80, 3.95, 4.10],
                "Discharge capacity [A.h]": [0.0, 0.10, 0.20, 0.15, 0.10, 0.05],
                "X-averaged cell temperature [C]": [25.0, 25.4, 25.9, 25.6, 25.3, 25.1],
                "Cycle": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
                "Step": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
            }
        )

    @pytest.fixture
    def mock_df_kelvin(self):
        return pl.DataFrame(
            {
                "Time [s]": [0.0, 1.0, 2.0],
                "Current [A]": [1.0, 1.0, -0.5],
                "Voltage [V]": [4.10, 3.90, 4.00],
                "Discharge capacity [A.h]": [0.0, 0.10, 0.05],
                "X-averaged cell temperature [K]": [298.15, 298.55, 299.05],
            }
        )

    def test_normalizes_expected_columns(self, mock_df):
        out = NORMALIZERS["pybamm"].normalize(mock_df, validate=False)
        for mr in (
            "test_time_second",
            "voltage_volt",
            "current_ampere",
            "net_capacity_ah",
            "temperature_t1_celsius",
            "cycle_count",
            "step_id",
        ):
            assert getattr(COLUMN_ONTOLOGY, mr).formatted_label in out.columns

    def test_unit_compatible_columns_pass_through(self, mock_df):
        """Time/voltage/temperature are unit-compatible 1:1 (s, V, C) — no scaling or sign flip."""
        out = NORMALIZERS["pybamm"].normalize(mock_df, validate=False)
        assert out["Test Time / s"].to_list() == mock_df["Time [s]"].to_list()
        assert out["Voltage / V"].to_list() == mock_df["Voltage [V]"].to_list()
        assert out["Temperature T1 / degC"].to_list() == mock_df["X-averaged cell temperature [C]"].to_list()

    def test_kelvin_temperature_converts_to_celsius(self, mock_df_kelvin):
        """Kelvin exports normalize to the BDF Celsius column."""
        out = NORMALIZERS["pybamm"].normalize(mock_df_kelvin, validate=False)
        assert out["Temperature T1 / degC"].to_list() == pytest.approx([25.0, 25.4, 25.9])

    def test_current_sign_flipped_to_charge_positive(self, mock_df):
        """PyBaMM is discharge-positive; BDF is charge-positive, so current is negated."""
        out = NORMALIZERS["pybamm"].normalize(mock_df, validate=False)
        assert out["Current / A"].to_list() == (-mock_df["Current [A]"]).to_list()

    def test_net_capacity_sign_flipped_from_discharge_capacity(self, mock_df):
        """PyBaMM "Discharge capacity" (Q-Q0, discharge-positive) negates to net_capacity_ah."""
        out = NORMALIZERS["pybamm"].normalize(mock_df, validate=False)
        assert out["Net Capacity / Ah"].to_list() == (-mock_df["Discharge capacity [A.h]"]).to_list()

    def test_cycle_and_step_cast_to_int(self, mock_df):
        out = NORMALIZERS["pybamm"].normalize(mock_df, validate=False)
        assert out["Cycle Count / 1"].to_list() == mock_df["Cycle"].cast(pl.Int64).to_list()
        assert out["Step ID"].to_list() == mock_df["Step"].cast(pl.Int64).to_list()

    def test_test_time_second_monotonic_nondecreasing(self, mock_df):
        out = NORMALIZERS["pybamm"].normalize(mock_df, validate=False)
        t = out["Test Time / s"]
        assert (t.diff().drop_nulls() >= 0).all()

    def test_net_capacity_reflects_charge_discharge_sign(self, mock_df):
        """In BDF convention net capacity falls step-to-step while discharging
        (now negative current) and rises while charging (now positive current)."""
        out = NORMALIZERS["pybamm"].normalize(mock_df, validate=False)
        cap = out["Net Capacity / Ah"]
        current = out["Current / A"]
        d_cap = cap.diff()
        # within the discharge run net capacity is non-increasing
        assert (d_cap.filter(current < 0).drop_nulls() <= 0).all()
        # within the charge run net capacity is non-decreasing
        assert (d_cap.filter(current > 0).drop_nulls() >= 0).all()


class TestDeprecatedRedirects:
    """Deprecated on-disk labels must land on their dcterms:isReplacedBy target."""

    def test_renamed_deprecated_columns_survive_read(self):
        """Regression (#52-era data loss): step_capacity_ah / step_energy_wh headers,
        whose replacements have a different base name, were silently dropped by the
        base-name heuristic; the isReplacedBy link routes them correctly."""
        df = pl.DataFrame(
            {
                "test_time_second": [0.0, 1.0],
                "voltage_volt": [3.7, 3.6],
                "current_ampere": [0.1, 0.1],
                "step_capacity_ah": [0.5, 0.6],
                "step_energy_wh": [1.5, 1.7],
            }
        )
        out = BDF_NORMALIZER.normalize(df, validate=False)
        assert out["Step Cumulative Capacity / Ah"].to_list() == [0.5, 0.6]
        assert out["Step Cumulative Energy / Wh"].to_list() == [1.5, 1.7]

    def test_every_deprecated_notation_resolves_to_replacement_label(self):
        """Exhaustive: each deprecated notation header resolves to its replacement's label."""
        for mr, q in COLUMN_ONTOLOGY:
            if not q.deprecated:
                continue
            target = COLUMN_ONTOLOGY.get(q.replaced_by)
            assert target is not None, mr
            resolved = BDF_NORMALIZER.resolve([q.effective_notation])
            assert resolved.get(q.replaced_by) is not None, f"{q.effective_notation} did not resolve to {q.replaced_by}"
