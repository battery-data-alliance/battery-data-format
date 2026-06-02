"""Unit tests for the DataSource model (bdf.datasources).

Covers JSON round-trip, match_ext/match_magic, score, EXT_TO_READER
single-valuedness, and DataSource construction guards.
"""

from __future__ import annotations

import pytest

import bdf.datasources as D
from bdf.datasources import (
    DATASOURCES,
    DataSource,
    build_ext_to_reader,
)
from bdf.normalizers import NORMALIZERS, Normalizer, Syn
from bdf.readers import DelimTxtReader, ExcelReader


@pytest.mark.parametrize("ds", list(DATASOURCES.values()), ids=list(DATASOURCES))
def test_datasource_json_round_trip(ds: DataSource) -> None:
    """Every built-in DataSource survives model_dump_json → model_validate_json."""
    assert DataSource.model_validate_json(ds.model_dump_json()) == ds


def test_match_ext() -> None:
    """match_ext is true only for the source's distinctive (case-insensitive) extensions."""
    assert D.BIOLOGIC_MPT.match_ext(".mpt")
    assert D.BIOLOGIC_MPT.match_ext(".MPT")
    assert not D.BIOLOGIC_MPT.match_ext(".csv")
    assert not D.ARBIN_CSV.match_ext(".csv")  # arbin declares no distinctive exts


def test_match_magic_text() -> None:
    """match_magic matches case-insensitive string tokens against decoded head text."""
    assert D.MACCOR_CSV.match_magic(b"...Date of Test:,2021...")
    assert not D.MACCOR_CSV.match_magic(b"nothing relevant here")
    assert not D.ARBIN_CSV.match_magic(b"anything")  # no magic declared


def test_match_magic_bytes_token() -> None:
    """match_magic supports raw byte tokens (binary-safe), not just str tokens."""
    ds = DataSource(id="bin", magic=(b"\x89PNG",), normalizer=Normalizer(), reader=DelimTxtReader())
    assert ds.match_magic(b"\x89PNG\r\n\x1a\n rest")
    assert not ds.match_magic(b"plain text")


def test_score_delegates_to_normalizer() -> None:
    """DataSource.score counts headers resolved by the referenced normalizer."""
    ds = DataSource(id="x", normalizer=Normalizer(voltage_volt=[Syn("v")]), reader=DelimTxtReader())
    assert ds.score(["v"]) == 1
    assert ds.score(["nope"]) == 0


def test_neware_csv_and_xlsx_share_normalizer() -> None:
    """NEWARE_CSV and NEWARE_XLSX reference one shared normalizer (different engines)."""
    assert D.NEWARE_CSV.normalizer is D.NEWARE_XLSX.normalizer is NORMALIZERS["neware"]
    assert D.NEWARE_CSV.reader.name == "txt"
    assert D.NEWARE_XLSX.reader.name == "excel"


def test_binary_engine_magic_warns() -> None:
    """Setting magic/metadata on a binary-engine source warns (text-only relevance)."""
    with pytest.warns(UserWarning, match="binary reader"):
        DataSource(id="b", magic=("foo",), normalizer=Normalizer(), reader=ExcelReader())


def test_binary_reader_default_metadata_no_warn() -> None:
    """Binary-reader source with no magic and default metadata fields does not warn."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        DataSource(id="b", normalizer=Normalizer(), reader=ExcelReader())


def test_conflicting_extension_raises() -> None:
    """A fixture where two readers claim the same extension raises ValueError."""
    csv_src = DataSource(id="c", exts=(".zct",), normalizer=Normalizer(), reader=DelimTxtReader())
    xls_src = DataSource(id="x", exts=(".zct",), normalizer=Normalizer(), reader=ExcelReader())
    with pytest.raises(ValueError, match="claimed by readers"):
        build_ext_to_reader({"c": csv_src, "x": xls_src})
