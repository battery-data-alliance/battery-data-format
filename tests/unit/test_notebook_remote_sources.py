"""Enforce that every URL an example notebook references is explicitly declared.

The ``integration`` CI job runs the network-marked notebook tests against a cache
primed by ``scripts/warm_cache.py``. That warming reads the URL set from
``ALL_CASES`` plus ``examples/remote_sources.REMOTE_DATA_SOURCES``. If a notebook
fetches a remote file in neither, the cache misses and the matrix re-downloads it
(the herd this change exists to prevent).

This test closes that gap with a total declare-and-verify partition: it
statically extracts every URL the notebooks reference and requires each to be
either a declared remote *data* source (warmed) or a declared *non-data* URL
(``NON_DATA_URLS`` — DOIs, landing pages, vocabulary IRIs that are never
fetched). Nothing is classified by host or path; an unaccounted URL fails the
test until it is triaged into one bucket or the other. It performs no network and
runs in the fast unit lane, so a stray source is caught before the network jobs
start.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# nbformat parses the notebook structure; IPython's input transformer (the same
# one Jupyter applies) rewrites line magics / ``!`` shell escapes into valid
# Python, so each cell parses with ``ast`` without bespoke magic handling.
nbformat = pytest.importorskip("nbformat")
_TRANSFORMER = pytest.importorskip("IPython.core.inputtransformer2").TransformerManager()

from docs.examples.remote_sources import NON_DATA_URLS, REMOTE_DATA_SOURCES  # noqa: E402
from tests.integration.test_cases import ALL_CASES  # noqa: E402

_EXAMPLES_DIR = _REPO_ROOT / "docs" / "examples"

# URL substring, stopping at whitespace, quotes, angle/round brackets, backslash.
_URL_RE = re.compile(r"https?://[^\s\"'\\)<>]+")


def _declared_data_urls() -> set[str]:
    """Return the remote data URLs warm-cache primes.

    Returns:
        Set of URL strings from ``ALL_CASES`` (URL cases) and ``REMOTE_DATA_SOURCES``.
    """
    case_urls = {case.source for _id, case in ALL_CASES if case.is_url}
    return case_urls | set(REMOTE_DATA_SOURCES.values())


def _url_constants(cell_source: str) -> set[str]:
    """Extract URL string constants from one code cell via AST.

    The cell is first run through IPython's input transformer to turn line
    magics / ``!`` shell escapes into valid Python, then parsed with :mod:`ast`,
    which joins implicitly-concatenated string literals (e.g. a URL split across
    adjacent quoted lines) into a single constant. Falls back to a raw regex scan
    if the transformed cell still does not parse.

    Args:
        cell_source: The notebook cell's source (a single string).

    Returns:
        Set of URL strings found in the cell's string constants.
    """
    found: set[str] = set()
    try:
        tree = ast.parse(_TRANSFORMER.transform_cell(cell_source))
    except SyntaxError:
        return set(_URL_RE.findall(cell_source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(_URL_RE.findall(node.value))
    return found


def _notebook_urls() -> set[str]:
    """Return every URL referenced in any code cell across ``examples/*.ipynb``.

    Returns:
        Set of URL strings (no classification applied).
    """
    urls: set[str] = set()
    for nb_path in sorted(_EXAMPLES_DIR.glob("*.ipynb")):
        nb = nbformat.read(nb_path, as_version=4)
        for cell in nb.cells:
            if cell.cell_type == "code":
                urls |= _url_constants(cell.source)
    return urls


def test_notebook_url_extraction_count() -> None:
    """Canary on the extractor: the notebooks reference a known number of URLs.

    Currently 9: 5 fetched data files (all in ``REMOTE_DATA_SOURCES``, 2 of which
    also appear in ``ALL_CASES``) plus 4 non-data references (``NON_DATA_URLS``).
    Bump this when a notebook adds or removes a URL — a silent drop means the
    nbformat/IPython-transformer/AST extraction has regressed.
    """
    urls = _notebook_urls()
    assert len(urls) == 9, f"expected 9 notebook URLs, extracted {len(urls)}:\n" + "\n".join(
        f"  - {u}" for u in sorted(urls)
    )


def test_every_notebook_url_is_declared() -> None:
    """Every URL a notebook references must be declared in ``examples/remote_sources.py``.

    Declaration is required in ``REMOTE_DATA_SOURCES`` (or ``NON_DATA_URLS``)
    regardless of whether the URL is also covered by ``ALL_CASES`` — that module is
    the complete record of what the notebooks download, so a URL present only in
    ``ALL_CASES`` is still unaccounted here.
    """
    known = set(REMOTE_DATA_SOURCES.values()) | NON_DATA_URLS
    unaccounted = sorted(_notebook_urls() - known)
    assert not unaccounted, (
        "Notebook(s) reference URLs that are not declared. Triage each into "
        "examples/remote_sources.py — REMOTE_DATA_SOURCES if it is a file the "
        "notebook fetches (it will be warm-cached), or NON_DATA_URLS otherwise:\n"
        + "\n".join(f"  - {u}" for u in unaccounted)
    )


def test_data_and_non_data_are_disjoint() -> None:
    """A URL cannot be both a warmed data source and a declared non-data URL."""
    overlap = sorted(_declared_data_urls() & NON_DATA_URLS)
    assert not overlap, "URLs declared as both data and non-data:\n" + "\n".join(f"  - {u}" for u in overlap)


def test_no_stale_declared_remote_sources() -> None:
    """Every declared notebook data source must actually be used by a notebook."""
    used = _notebook_urls()
    stale = sorted(v for v in REMOTE_DATA_SOURCES.values() if v not in used)
    assert not stale, "REMOTE_DATA_SOURCES declares URLs no notebook references (remove them):\n" + "\n".join(
        f"  - {u}" for u in stale
    )


def test_no_stale_non_data_urls() -> None:
    """Every declared non-data URL must actually appear in a notebook."""
    used = _notebook_urls()
    stale = sorted(u for u in NON_DATA_URLS if u not in used)
    assert not stale, "NON_DATA_URLS declares URLs no notebook references (remove them):\n" + "\n".join(
        f"  - {u}" for u in stale
    )
