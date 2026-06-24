"""Declared remote data sources fetched by the example notebooks.

This is the single source of truth for every remote file the ``examples/*.ipynb``
notebooks download at execution time. ``scripts/warm_cache.py`` warms these (in
addition to the integration ``ALL_CASES`` URLs) so the ``integration`` CI job —
which runs the network-marked notebook tests — restores a fully primed cache and
downloads nothing.

The contract is enforced by ``tests/unit/test_notebook_remote_sources.py``: that
test extracts every remote data URL the notebooks reference and fails if any is
not declared here (or already covered by ``ALL_CASES``). Adding a new remote read
to a notebook therefore requires adding it here, which rotates the cache key and
keeps the warm-cache complete.

Keys are short, stable identifiers; values are the exact URL strings passed to
``bdf.read`` / ``resolve_source`` (the cache is keyed on ``sha256(url)``, so the
string must match byte-for-byte).
"""

from __future__ import annotations

REMOTE_DATA_SOURCES: dict[str, str] = {
    "notebook_biologic_mpt": (
        "https://zenodo.org/records/17289383/files/"
        "SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt"
    ),
    "notebook_digatron_csv": (
        "https://zenodo.org/records/17295469/files/"
        "FZJ__INR21700__20250606__HPPC__25degC__Digatron.csv"
    ),
    "notebook_neware_time_bug_csv": (
        "https://zenodo.org/records/17295469/files/"
        "SINTEF__SLPBA842124HV__2024-10-23__Rate_25degC__Neware__Time_Bug.csv"
    ),
    # Also fetched by parser notebooks; these happen to overlap ALL_CASES — listed
    # here too so this dict is the complete record of what the notebooks download.
    "notebook_biologic_mpt_content": (
        "https://zenodo.org/api/records/18986774/files/"
        "SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt/content"
    ),
    "notebook_neware_nda_content": (
        "https://zenodo.org/api/records/18986774/files/"
        "SINTEF__G20M7-202512-Gru6mV__20251228__C30__25degC__Neware.nda/content"
    ),
}
"""Mapping of identifier -> exact remote URL fetched by an example notebook.

Lists **every** remote file the notebooks download, so adding a remote read to a
notebook means adding it here — no need to know whether it also appears in the
integration ``ALL_CASES``. Overlap with ``ALL_CASES`` is fine: warming and the
cache key de-duplicate via set union.
"""


PINNED_ONTOLOGY_VERSIONS: tuple[str, ...] = ("1.1.0",)
"""Ontology release tags an example notebook pins via ``ColumnOntology.load_version``.

These never appear as literal URLs in a notebook (the version is interpolated
into ``_BDF_RELEASE_URL_TMPL`` at call time), so the URL-extraction contract
above can't catch them. ``scripts/warm_cache.py`` fetches each version into the
``BDF_CACHE_DIR``-backed ontology cache ahead of the network-marked notebook
tests, which load from that cache with no network call.
"""


NON_DATA_URLS: frozenset[str] = frozenset(
    {
        # Metadata example: dataset DOI and landing page (not fetched).
        "https://doi.org/10.5281/zenodo.16994937#digatron-csv-li-ion-hppc",
        "https://zenodo.org/records/17295469",
        # SPARQL / vocabulary IRIs, not network reads.
        "https://schema.org/Dataset",
        "https://schema.org/name",
    }
)
"""URLs the notebooks reference but do **not** fetch as data files.

Declared explicitly so every URL in a notebook is accounted for: the verifying
test requires each extracted URL to be either a declared data source (warmed) or
listed here. Nothing is classified by host or path — an unclassified URL fails
the test until it is triaged into one bucket or the other.
"""
