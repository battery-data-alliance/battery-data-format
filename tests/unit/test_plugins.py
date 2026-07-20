"""Unit tests for Plugin model, PLUGINS, and detection functions."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from bdf.plugins import (
    BDF_NORMALIZER,
    BIOLOGIC_MPT,
    MACCOR_CSV,
    NDA_NORMALIZER,
    NEWARE_CSV,
    PLUGINS,
    Plugin,
    PluginDict,
    detect,
    detect_from_columns,
    detect_from_ext_or_magic_bytes,
    detect_from_metadata,
    dump_plugins,
    load_plugins,
)
from bdf.spec import COLUMN_ONTOLOGY
from bdf.table_normalizers import NORMALIZERS, Syn, TableNormalizer
from bdf.table_parsers import DelimTxtParser


class TestPluginDict:
    def test_plugin_dict_exact_match(self) -> None:
        """PluginDict returns values for exact key matches."""
        d = PluginDict({"apple": 1, "banana": 2})
        assert d["apple"] == 1
        assert d["banana"] == 2

    def test_plugin_dict_missing_key_exact(self) -> None:
        """PluginDict raises KeyError for non-existent keys with no close matches."""
        d = PluginDict({"apple": 1, "banana": 2})
        with pytest.raises(KeyError, match="No plugin named"):
            d["xyz"]

    def test_plugin_dict_close_match(self) -> None:
        """PluginDict suggests close matches on KeyError."""
        d = PluginDict({"apple": 1, "apricot": 2})
        with pytest.raises(KeyError, match="did you mean:"):
            d["appl"]

    def test_plugin_dict_multiple_matches(self) -> None:
        """PluginDict shows up to 3 close matches."""
        d = PluginDict({"apple": 1, "apricot": 2, "application": 3, "apply": 4})
        with pytest.raises(KeyError) as exc_info:
            d["appl"]
        error_msg = str(exc_info.value)
        assert "did you mean:" in error_msg
        # Should suggest multiple matches
        assert error_msg.count("'") >= 3

    def test_plugin_dict_custom_cutoff(self) -> None:
        """PluginDict respects custom similarity cutoff."""
        d = PluginDict({"apple": 1}, cutoff=0.9)
        with pytest.raises(KeyError, match="No plugin named"):
            d["appl"]

    def test_plugin_dict_plugins_exact(self) -> None:
        """PLUGINS dict provides exact access for valid plugin IDs."""
        assert PLUGINS["arbin_csv"] is not None
        assert PLUGINS["neware_csv"] is not None
        assert PLUGINS["biologic_mpt"] is not None

    def test_plugin_dict_plugins_typo(self) -> None:
        """PLUGINS dict suggests close matches for typos."""
        with pytest.raises(KeyError, match="did you mean:"):
            PLUGINS["neware_cs"]  # Missing 'v'

    def test_plugin_dict_plugins_all_registered(self) -> None:
        """All built-in plugins are accessible in PLUGINS without suggestions."""
        expected_ids = {
            "arbin_csv",
            "basytec_txt",
            "biologic_mpt",
            "digatron_csv",
            "landt_csv",
            "landt_txt",
            "maccor_csv",
            "neware_csv",
            "neware_xlsx",
            "novonix_csv",
            "neware_nda",
            "bdf_csv",
            "bdf_parquet",
        }
        for pid in expected_ids:
            assert PLUGINS[pid] is not None

    @pytest.mark.parametrize("ds", list(PLUGINS.values()), ids=list(PLUGINS))
    def test_plugin_json_round_trip(self, ds: Plugin) -> None:
        """Every built-in Plugin survives model_dump_json → model_validate_json."""
        assert Plugin.model_validate_json(ds.model_dump_json()) == ds

    def test_plugin_defaults_metadata_to_inert_parser(self) -> None:
        """A Plugin built from a table_parser alone gets an inert base MetadataParser."""
        p = Plugin(table_parser=DelimTxtParser(normalizer=NORMALIZERS["arbin"]))
        assert p.table_parser.normalizer is NORMALIZERS["arbin"]
        assert p.metadata_parser.kind == "base"

    def test_plugin_legacy_fields_raise(self) -> None:
        """Passing legacy top-level reader/normalizer fields raises ValidationError."""

        with pytest.raises(ValidationError):
            Plugin(normalizer=TableNormalizer(), reader=DelimTxtParser())  # type: ignore[call-arg]


class TestMatchesExt:
    """TableParser.matches_ext."""

    def test_matches_ext_unique_ext(self) -> None:
        """A TableParser with unique_exts matches its own extension."""
        assert DelimTxtParser(unique_exts=frozenset({".mpt"})).matches_ext(".mpt") is True

    def test_matches_ext_case_insensitive(self) -> None:
        """TableParser.matches_ext is case-insensitive."""
        assert DelimTxtParser().matches_ext(".CSV") is True


class TestDetectFromExtOrMagicBytes:
    def test_detect_from_ext_distinctive_ext(self) -> None:
        """detect_from_ext_or_magic_bytes returns the expected plugin for a distinctive extension."""
        result = detect_from_ext_or_magic_bytes("data.mpt")
        assert len(result) == 1
        assert "biologic_mpt" in result
        assert result["biologic_mpt"].table_parser is BIOLOGIC_MPT.table_parser

    def test_detect_from_ext_shared_ext(self) -> None:
        """detect_from_ext_or_magic_bytes returns all candidates for a shared extension."""
        result = detect_from_ext_or_magic_bytes("data.csv")
        assert len(result) >= 1
        assert all(p.table_parser.matches_ext(".csv") for p in result.values())

    def test_detect_from_ext_with_cands(self) -> None:
        """detect_from_ext_or_magic_bytes respects a custom candidate dict."""
        cands = {"biologic_mpt": BIOLOGIC_MPT, "neware_csv": NEWARE_CSV}
        result = detect_from_ext_or_magic_bytes("data.mpt", cands)
        assert list(result) == ["biologic_mpt"]

    def test_detect_from_ext_url_no_fetch(self) -> None:
        """Extension is parsed from the URL string alone — no network I/O."""
        result = detect_from_ext_or_magic_bytes("https://example.com/data.mpt")
        assert "biologic_mpt" in result

    def test_detect_from_ext_unmatched_ext_falls_back_to_magic_bytes(self, tmp_path: Path) -> None:
        """An unrecognised extension falls back to magic-bytes detection on file content."""
        p = tmp_path / "data.xyz"
        p.write_bytes(b"NEWARE" + b"\x00" * 20)
        result = detect_from_ext_or_magic_bytes(str(p))
        assert "neware_nda" in result

    def test_detect_from_ext_unmatched_ext_unrecognised_binary_raises(self, tmp_path: Path) -> None:
        """An unrecognised extension with unrecognised binary content raises, not silently DelimTxtParser."""
        p = tmp_path / "data.xyz"
        p.write_bytes(b"\x00\x01\x02\xff\xfe" * 20)
        with pytest.raises(ValueError, match="no candidate's magic bytes matched"):
            detect_from_ext_or_magic_bytes(str(p))


class TestDetectFromMetadata:
    def test_detect_from_metadata_match_narrows(self, tmp_path: Path) -> None:
        """Only plugins whose metadata parser matches are returned."""
        p = tmp_path / "data.mpt"
        p.write_text("BT-Lab ASCII FILE\nsome biologic content\n")
        result = detect_from_metadata(p)
        assert all(plugin.metadata_parser.matches(p) for plugin in result.values())
        assert len(result) < len(PLUGINS)

    def test_detect_from_metadata_no_match_returns_unchanged(self, tmp_path: Path) -> None:
        """When nothing matches, the candidate dict is returned unchanged."""
        p = tmp_path / "data.csv"
        p.write_text("totally generic content with no magic tokens\n")
        cands = dict(PLUGINS)
        result = detect_from_metadata(p, cands)
        assert result is cands

    def test_detect_from_metadata_maccor_magic(self, tmp_path: Path) -> None:
        """detect_from_metadata recognises the Maccor preamble magic."""
        p = tmp_path / "data.csv"
        p.write_text("Date of Test:,2021-01-01\n")
        result = detect_from_metadata(p, {"maccor_csv": MACCOR_CSV})
        assert "maccor_csv" in result


class TestDetectFromColumns:
    def test_detect_from_columns_clear_winner(self, tmp_path: Path) -> None:
        """detect_from_columns returns the plugin whose normalizer scores highest."""
        p = tmp_path / "data.csv"
        rows = "\n".join("0.1,3.5,1" for _ in range(6))
        p.write_text(f"time/s,Ewe/V,I/mA\n{rows}\n")
        plugin_id, plugin = detect_from_columns(p)
        assert plugin_id == "biologic_mpt"
        assert plugin.table_parser.normalizer is NORMALIZERS["biologic"]

    def test_detect_from_columns_zero_score_raises(self, tmp_path: Path) -> None:
        """detect_from_columns raises when no candidate scores above zero."""
        p = tmp_path / "data.csv"
        rows = "\n".join("1,2,3" for _ in range(6))
        p.write_text(f"unknown_a,unknown_b,unknown_c\n{rows}\n")
        with pytest.raises(ValueError, match="no candidate scored"):
            detect_from_columns(p)

    def test_detect_from_columns_tied_raises(self, tmp_path: Path) -> None:
        """detect_from_columns raises when the top score is tied."""

        p = tmp_path / "tied.csv"
        rows = "\n".join("1,2" for _ in range(6))
        p.write_text(f"col_a,col_b\n{rows}\n")
        cands = {
            "a": Plugin(
                table_parser=DelimTxtParser(normalizer=TableNormalizer(voltage_volt=(Syn(hdr="col_a"),)), separator=",")
            ),
            "b": Plugin(
                table_parser=DelimTxtParser(
                    normalizer=TableNormalizer(current_ampere=(Syn(hdr="col_a"),)), separator=","
                )
            ),
        }
        with pytest.raises(ValueError, match="ambiguous"):
            detect_from_columns(p, cands)


class TestDetectIntegration:
    """detect() integration."""

    def test_neware_csv_detects_by_extension_and_headers(self, tmp_path: Path) -> None:
        """A neware-style CSV resolves to neware_csv via the shared normalizer scoring."""
        p = tmp_path / "neware.csv"
        rows = "\n".join(f"{i},{i},{i},{i},{i}" for i in range(6))
        p.write_text(f"Date,Total Time,Cycle,Step,Record\n{rows}\n")
        plugin_id, plugin = detect(p)
        assert plugin_id == "neware_csv"
        assert plugin.table_parser.normalizer is NORMALIZERS["neware"]


class TestDetectFromExtCompound:
    """detect_from_ext_or_magic_bytes compound suffix."""

    @pytest.mark.parametrize(
        "filename,expected_id",
        [
            ("cell.bdf.csv", "bdf_csv"),
            ("cell.csv", "bdf_csv"),
            ("cell.parquet", "bdf_parquet"),
            ("cell.nda", "neware_nda"),
            ("cell.ndax", "neware_nda"),
        ],
        ids=["bdf_csv_compound", "csv_includes_bdf", "parquet", "nda", "ndax"],
    )
    def test_detect_from_ext_compound(self, filename: str, expected_id: str) -> None:
        """detect_from_ext_or_magic_bytes matches compound and standard extensions to expected plugins."""
        result = detect_from_ext_or_magic_bytes(filename)
        assert expected_id in result
        if filename == "cell.bdf.csv":
            assert set(result.keys()) == {"bdf_csv"}
        elif filename == "cell.csv":
            assert len(result) > 1


class TestNdaNormalizer:
    @pytest.mark.parametrize(
        "src_col,src_val,expected_col,expected_val",
        [
            ("current_mA", [1000.0], "Current / A", 1.0),
            ("current_A", [2.0], "Current / A", 2.0),
            ("capacity_mAh", [-500.0], "Step Net Capacity / Ah", -0.5),
            ("voltage_V", [3.7], "Voltage / V", 3.7),
            ("cycle_count", [5], "Cycle Count / 1", 5),
        ],
        ids=["current_mA_scale", "current_A_passthrough", "capacity_scale", "voltage_match", "cycle_int"],
    )
    def test_nda_normalizer(self, src_col: str, src_val: list, expected_col: str, expected_val: float) -> None:
        """NDA_NORMALIZER maps vendor columns to BDF labels with correct scaling."""
        lf = pl.DataFrame({src_col: src_val}).lazy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = NDA_NORMALIZER.normalize(lf, validate=False).collect()
        assert expected_col in out.columns
        assert pytest.approx(out[expected_col][0]) == expected_val


class TestBDFNormalizer:
    def test_all_non_deprecated_mr_names_present(self):
        """BDF_NORMALIZER covers all non-deprecated mr_names in the ontology."""
        bdf_fields = {mr_name for mr_name, _ in BDF_NORMALIZER}
        known_fields = set(TableNormalizer.model_fields)
        ontology_non_deprecated = {mr_name for mr_name, q in COLUMN_ONTOLOGY if not q.deprecated}
        expected = ontology_non_deprecated & known_fields
        assert expected == bdf_fields

    def test_voltage_passthrough(self):
        """Voltage / V passes through unchanged."""
        lf = pl.DataFrame({"Voltage / V": [3.7]}).lazy()
        out = BDF_NORMALIZER.normalize(lf, validate=False).collect()
        assert "Voltage / V" in out.columns
        assert pytest.approx(out["Voltage / V"][0]) == 3.7

    def test_current_mA_conversion(self):
        """Current / mA is converted to Current / A."""
        lf = pl.DataFrame({"Current / mA": [500.0]}).lazy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = BDF_NORMALIZER.normalize(lf, validate=False).collect()
        assert "Current / A" in out.columns
        assert pytest.approx(out["Current / A"][0]) == 0.5

    def test_bdf_normalizer_scores_highest_on_bdf_headers(self):
        """BDF_NORMALIZER scores higher than any vendor normalizer on BDF headers."""
        bdf_headers = [
            "Test Time / s",
            "Voltage / V",
            "Current / A",
            "Cycle Count / 1",
        ]
        bdf_score = BDF_NORMALIZER.score_columns(bdf_headers)
        for name, norm in [
            ("arbin", PLUGINS["arbin_csv"].table_parser.normalizer),
            ("neware", PLUGINS["neware_csv"].table_parser.normalizer),
            ("biologic", PLUGINS["biologic_mpt"].table_parser.normalizer),
        ]:
            vendor_score = norm.score_columns(bdf_headers)
            assert bdf_score > vendor_score, f"BDF_NORMALIZER should outscore {name}"


class TestWithNormalizer:
    def test_with_normalizer_replaces_normalizer(self) -> None:
        """with_normalizer() installs the new normalizer into the table_parser."""
        new_norm = TableNormalizer(voltage_volt=(Syn(hdr="U"),))
        new_plugin = NEWARE_CSV.with_normalizer(new_norm)
        assert new_plugin.table_parser.normalizer is new_norm

    def test_with_normalizer_preserves_other_parser_settings(self) -> None:
        """with_normalizer() leaves every other table_parser field untouched."""
        new_norm = TableNormalizer()
        new_plugin = BIOLOGIC_MPT.with_normalizer(new_norm)
        assert new_plugin.table_parser.encoding == BIOLOGIC_MPT.table_parser.encoding
        assert new_plugin.table_parser.unique_exts == BIOLOGIC_MPT.table_parser.unique_exts
        assert new_plugin.table_parser.kind == BIOLOGIC_MPT.table_parser.kind

    def test_with_normalizer_metadata_unchanged(self) -> None:
        """with_normalizer() does not touch metadata_parser."""
        new_plugin = BIOLOGIC_MPT.with_normalizer(TableNormalizer())
        assert new_plugin.metadata_parser == BIOLOGIC_MPT.metadata_parser

    def test_with_normalizer_returns_frozen_plugin(self) -> None:
        """The returned Plugin is still frozen."""
        new_plugin = NEWARE_CSV.with_normalizer(TableNormalizer())
        with pytest.raises(ValidationError, match="frozen"):
            new_plugin.table_parser = DelimTxtParser()  # type: ignore[misc]


class TestLoadDumpPlugins:
    def test_json_round_trip(self, tmp_path: Path) -> None:
        """dump_plugins -> load_plugins round-trips through JSON unchanged."""
        plugins = {"neware_csv": NEWARE_CSV, "biologic_mpt": BIOLOGIC_MPT}
        path = tmp_path / "plugins.json"
        dump_plugins(plugins, path)
        assert load_plugins(path) == plugins

    def test_yaml_round_trip(self, tmp_path: Path) -> None:
        """dump_plugins -> load_plugins round-trips through YAML unchanged (skipped without PyYAML)."""
        pytest.importorskip("yaml")
        plugins = {"neware_csv": NEWARE_CSV}
        path = tmp_path / "plugins.yaml"
        dump_plugins(plugins, path)
        assert load_plugins(path) == plugins

    def test_yaml_without_pyyaml_raises_import_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both load_plugins and dump_plugins raise ImportError with a pip hint when PyYAML is absent."""
        import bdf.plugins as plugins_mod

        monkeypatch.setattr(plugins_mod, "_HAS_YAML", False)
        path = tmp_path / "plugins.yaml"
        path.write_text("{}")
        with pytest.raises(ImportError, match="pip install PyYAML"):
            load_plugins(path)
        with pytest.raises(ImportError, match="pip install PyYAML"):
            dump_plugins({"neware_csv": NEWARE_CSV}, path)

    def test_load_plugins_invalid_data_raises_validation_error(self, tmp_path: Path) -> None:
        """load_plugins surfaces pydantic ValidationError for malformed entries."""
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"bad": {"table_parser": {"kind": "not_a_kind"}}}))
        with pytest.raises(ValidationError):
            load_plugins(path)

    def test_dump_plugins_omits_null_fields(self, tmp_path: Path) -> None:
        """dump_plugins excludes unset (None) fields so output stays readable."""
        path = tmp_path / "plugins.json"
        dump_plugins({"neware_csv": NEWARE_CSV}, path)
        data = json.loads(path.read_text())
        assert "separator" not in data["neware_csv"]["table_parser"]


class TestArbinXlsxDetection:
    """arbin_xlsx vs neware_xlsx disambiguation on shared .xlsx extension."""

    @staticmethod
    def _workbook(tmp_path, sheet, headers, name):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet
        ws.append(headers)
        ws.append([1] * len(headers))
        path = tmp_path / name
        wb.save(path)
        return path

    def test_arbin_workbook_detects_arbin_xlsx(self, tmp_path):
        pytest.importorskip("fastexcel")
        path = self._workbook(
            tmp_path,
            "Channel_1-002",
            ["Data_Point", "Test_Time(s)", "Voltage(V)", "Current(A)", "Step_Index"],
            "arbin.xlsx",
        )
        plugin_id, _ = detect(path)
        assert plugin_id == "arbin_xlsx"

    def test_neware_workbook_still_detects_neware_xlsx(self, tmp_path):
        pytest.importorskip("fastexcel")
        path = self._workbook(
            tmp_path,
            "record",
            ["Time", "Voltage(V)", "Current(A)", "Step Type"],
            "neware.xlsx",
        )
        plugin_id, _ = detect(path)
        assert plugin_id == "neware_xlsx"
