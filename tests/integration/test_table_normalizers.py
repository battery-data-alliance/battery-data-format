"""Integration tests for bdf.table_normalizers — per-synonym real-file coverage guard."""

from __future__ import annotations

from typing import Iterator

import pytest

from bdf.plugins import PLUGINS
from bdf.spec import COLUMN_ONTOLOGY
from bdf.table_normalizers import NORMALIZERS, DateTimeSyn, SynUnion, TableNormalizer
from integration.test_cases import ALL_CASES

# ---------------------------------------------------------------------------
# Per-synonym coverage machinery
#
# A synonym is "covered" if some real-file ``source_header`` recorded across
# the test cases sharing that synonym's normalizer matches it. The header
# pool is built from authored test case strings only. BDF is excluded from
# this guard because its aliases are ontology-derived round-trip forms rather
# than vendor sample headers.
# ---------------------------------------------------------------------------

_KEY_BY_NORMALIZER: dict[TableNormalizer, str] = {norm: key for key, norm in NORMALIZERS.items()}
_PYBAMM_EXPORTED_HEADERS: frozenset[str] = frozenset(
    {
        "Time [s]",
        "Current [A]",
        "Voltage [V]",
        "Discharge capacity [A.h]",
        "X-averaged cell temperature [C]",
        "X-averaged cell temperature [K]",
    }
)


def pool(key: str) -> frozenset[str]:
    """Return all recorded source headers for the normalizer registered under ``key``.

    For vendor normalizers this is the authored ``ColExpect.source_header`` set.
    BDF is excluded from the per-synonym sample-data guard, so it has no pool.

    Args:
        key: A :data:`bdf.table_normalizers.NORMALIZERS` key (e.g. ``"arbin"``).

    Returns:
        Frozenset of source headers a real file could carry for that normalizer.
    """
    if key == "pybamm":
        # PyBaMM is normalized from an exported in-memory solution dataframe, not a
        # plugin-backed file sample. These authored headers mirror the export
        # contract exercised in ``test_pybamm_exports.py``.
        return _PYBAMM_EXPORTED_HEADERS
    headers: set[str] = set()
    for _, case in ALL_CASES:
        if case.expected_columns is None:
            continue
        norm = PLUGINS[case.plugin_id].table_parser.normalizer
        if _KEY_BY_NORMALIZER.get(norm) != key:
            continue
        for exp in case.expected_columns.values():
            headers.add(exp.source_header)
    return frozenset(headers)


def synonym_covered(key: str, mr: str, syn: SynUnion) -> bool:
    """Return whether ``syn`` matches any recorded header in ``pool(key)``.

    Args:
        key: The normalizer key whose header pool to test against.
        mr: The BDF ``mr_name`` the synonym is declared for (supplies the target unit).
        syn: The synonym (``Syn`` or ``DateTimeSyn``) to test.

    Returns:
        True if some pooled header matches the synonym.
    """
    unit = getattr(COLUMN_ONTOLOGY, mr).unit
    for header in pool(key):
        if isinstance(syn, DateTimeSyn):
            if syn.syn.exact_match(header):
                return True
        elif syn.match(header, unit) is not None:
            return True
    return False


def iter_synonyms() -> Iterator[tuple[str, str, SynUnion, str]]:
    """Yield ``(key, mr, syn, exemplar)`` for every synonym across all normalizers.

    ``exemplar`` is the synonym's pattern string, suffixed with ``#n`` when a
    pattern repeats within one normalizer so that ``(key, exemplar)`` stays unique.

    Yields:
        Tuples of ``(normalizer key, mr_name, synonym, unique exemplar)``.
    """
    for key, norm in NORMALIZERS.items():
        seen: dict[str, int] = {}
        for mr, field_val in norm:
            if not isinstance(field_val, tuple):
                continue
            for syn in field_val:
                exemplar = syn.syn.hdr if isinstance(syn, DateTimeSyn) else syn.hdr
                n = seen.get(exemplar, 0)
                seen[exemplar] = n + 1
                unique_exemplar = exemplar if n == 0 else f"{exemplar}#{n}"
                yield key, mr, syn, unique_exemplar


def _build_synonym_coverage_params() -> list:
    """Build parametrized test cases for synonym coverage, marking assumed synonyms as xfail.

    Assumed synonyms (Syn.assumed=True) have no sample data in the test corpus and are
    expected to fail. BDF synonyms are omitted because they are ontology-generated
    aliases validated elsewhere, not vendor sample headers. Covered synonyms (with
    sample headers in the corpus) must pass.

    Returns:
        List of pytest.param objects with xfail marks for assumed synonyms.
    """
    params = []
    for key, mr, syn, exemplar in iter_synonyms():
        if key == "bdf":
            continue
        assumed = syn.syn.assumed if isinstance(syn, DateTimeSyn) else syn.assumed
        marks = (
            (pytest.mark.xfail(strict=True, reason="Syn.assumed=True — no sample data exercises this synonym"),)
            if assumed
            else ()
        )
        params.append(pytest.param(key, mr, syn, marks=marks, id=f"{key}-{exemplar}"))
    return params


_COVERAGE_PARAMS = _build_synonym_coverage_params()


@pytest.mark.parametrize("key,mr,syn", _COVERAGE_PARAMS)
def test_synonym_coverage(key: str, mr: str, syn) -> None:
    """Every declared synonym is matched by a real-file source header in the test cases."""
    assert synonym_covered(key, mr, syn), f"{key}.{mr}: synonym not covered by any recorded header"
