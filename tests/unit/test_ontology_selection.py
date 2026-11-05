from __future__ import annotations

import warnings

import polars as pl
import pytest

from bdf.spec import COLUMN_ONTOLOGY, ColumnOntology
from bdf.table_normalizers import BDF_NORMALIZER, Syn, TableNormalizer
from bdf.table_parsers import DelimTxtParser

# A release like the bundle, without ambient temperature.
OLDER = ColumnOntology(
    {name: q.model_copy(deep=True) for name, q in COLUMN_ONTOLOGY if name != "ambient_temperature_celsius"},
    "1.2.9",
)

# A release like the bundle in which Step Index is current, as in 1.1.0. The bundled release
# deprecates it, so TableNormalizer has no field for it.
STEP_INDEX = ColumnOntology(
    {
        **{name: q.model_copy(deep=True) for name, q in COLUMN_ONTOLOGY if name != "step_record_index"},
        "step_index": COLUMN_ONTOLOGY.step_index.model_copy(update={"deprecated": False, "replaced_by": ""}),
    },
    "1.2.9",
)
STEP_INDEX_LABEL = "Step Index / 1"


def test_a_quantity_the_release_lacks_is_an_unknown_column() -> None:
    normalizer = TableNormalizer(
        test_time_second=(Syn(hdr="t", source_unit="s"),),
        voltage_volt=(Syn(hdr="U", source_unit="V"),),
        current_ampere=(Syn(hdr="I", source_unit="A"),),
        ambient_temperature_celsius=(Syn(hdr="Temp", source_unit="degC"),),
    )
    df = pl.DataFrame({"t": [0.0], "U": [3.7], "I": [0.1], "Temp": [25.0]})
    with pytest.warns(UserWarning, match="'Temp' maps to ambient_temperature_celsius.*1.2.9"):
        assert "Temp" not in normalizer.normalize(df, ontology=OLDER).columns
    with pytest.warns(UserWarning, match="'Temp'"):
        assert "Temp" in normalizer.normalize(df, ontology=OLDER, include_unknown=True).columns


def test_a_header_that_a_defined_quantity_takes_is_not_called_outside_the_spec() -> None:
    # "X" matches ambient temperature, which OLDER lacks, and step time, declared after it.
    normalizer = TableNormalizer(
        test_time_second=(Syn(hdr="t", source_unit="s"),),
        voltage_volt=(Syn(hdr="U", source_unit="V"),),
        current_ampere=(Syn(hdr="I", source_unit="A"),),
        ambient_temperature_celsius=(Syn(hdr="X", source_unit="degC"),),
        step_time_second=(Syn(hdr="X", source_unit="s"),),
    )
    df = pl.DataFrame({"t": [0.0], "U": [3.7], "I": [0.1], "X": [1.0]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = normalizer.normalize(df, ontology=OLDER)
    assert OLDER.step_time_second.formatted_label in out.columns


def test_an_extended_bdf_normalizer_maps_another_release_after_a_json_round_trip() -> None:
    parser = DelimTxtParser(normalizer=BDF_NORMALIZER.extend(voltage_volt=Syn(hdr="U", source_unit="V")))
    loaded = DelimTxtParser.model_validate_json(parser.model_dump_json())
    assert loaded == parser
    df = pl.DataFrame({"Test Time / s": [0.0], "U": [3.7], "Current / A": [0.1], STEP_INDEX_LABEL: [1]})
    out = loaded.normalizer.normalize(df, ontology=STEP_INDEX)
    # The release's own label, and the synonym the extension added.
    assert {"Voltage / V", STEP_INDEX_LABEL} <= set(out.columns)


def test_the_labels_of_a_release_are_built_once() -> None:
    assert BDF_NORMALIZER.for_ontology(OLDER) is BDF_NORMALIZER.for_ontology(OLDER)
    assert BDF_NORMALIZER.for_ontology(OLDER) is not BDF_NORMALIZER.for_ontology(COLUMN_ONTOLOGY)
