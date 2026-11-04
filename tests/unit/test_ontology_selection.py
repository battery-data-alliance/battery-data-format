from __future__ import annotations

import polars as pl
import pytest

from bdf.spec import COLUMN_ONTOLOGY, ColumnOntology
from bdf.table_normalizers import Syn, TableNormalizer

# A release like the bundle, without ambient temperature.
OLDER = ColumnOntology(
    {name: q.model_copy(deep=True) for name, q in COLUMN_ONTOLOGY if name != "ambient_temperature_celsius"},
    "1.2.9",
)


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
