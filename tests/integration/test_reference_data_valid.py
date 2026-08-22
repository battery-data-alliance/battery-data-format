"""Gate: reference example data must satisfy the ontology's derived-column rules.

The ``docs/examples/reference/*.bdf.csv`` files are downloaded verbatim from
Zenodo (see ``scripts/generate_zenodo_reference_bdf.py``). Some historical
uploads are internally inconsistent with the ontology's derived-column
definitions (mislabeled ``step_index``, swapped ``cumulative``/``net`` columns,
a ``cycle_count`` equal to 2*pi, etc.). Those known-bad files are tracked in
``KNOWN_NONCOMPLIANT`` with their issue references.

This test enforces two things:
  1. Any reference file NOT in the known-bad set must be clean -- so newly
     added or refreshed reference data cannot silently introduce the same
     class of corruption.
  2. Any file IN the known-bad set must still be non-compliant -- so once a
     file is fixed upstream, this test fails and reminds us to drop it from
     the allowlist.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from bdf import validate_df

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "examples" / "reference"

# filename -> tracking issue(s). See #52 for the audit.
KNOWN_NONCOMPLIANT: dict[str, str] = {
    "FZJ__INR21700__20250606__HPPC__25degC__Digatron.bdf.csv": "#54, #55, #56",
    "SINTEF__LiGrR2032__2024-04-30__25degC__Landt.bdf.csv": "#54",
    "SINTEF__G20M7-202512-Gru6mV__20251228__C30__25degC__Neware.bdf.csv": "#54, #56",
    "SINTEF__SLPBA842124HV__2024-10-23__Rate_25degC__Neware__Time_Bug.bdf.csv": "#54",
    "SINTEF__NaCR32140-MP10-04__2025-08-25__CCCV_0p02C_25degC__BioLogic__Outlier_Bug.bdf.csv": "#55",
    "SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.bdf.csv": "#55",
}


def _reference_files() -> list[Path]:
    return sorted(REFERENCE_DIR.glob("*.bdf.csv"))


def _derived_issues(path: Path) -> list[str]:
    df = pd.read_csv(path)
    rep = validate_df(df, report=False, raise_on_error=False)
    return rep["derived"]["issues"]


@pytest.mark.skipif(not _reference_files(), reason="no reference data present")
@pytest.mark.parametrize("path", _reference_files(), ids=lambda p: p.name)
def test_reference_file_derived_consistency(path: Path) -> None:
    issues = _derived_issues(path)
    if path.name in KNOWN_NONCOMPLIANT:
        # Known-bad: must still be non-compliant. When fixed upstream this
        # assertion fails -> remove the file from KNOWN_NONCOMPLIANT.
        assert issues, (
            f"{path.name} is now clean; remove it from KNOWN_NONCOMPLIANT (tracked in {KNOWN_NONCOMPLIANT[path.name]})."
        )
    else:
        assert not issues, f"{path.name} violates ontology-defined derived-column rules:\n  - " + "\n  - ".join(issues)
