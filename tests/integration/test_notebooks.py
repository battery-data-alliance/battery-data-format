from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")
NotebookClient = nbclient.NotebookClient


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = ROOT / "docs" / "examples"
NOTEBOOKS = sorted(EXAMPLES_DIR.glob("*.ipynb"))

SKIP_NOTEBOOKS = os.getenv("BDF_SKIP_NOTEBOOKS", "").lower() in {"1", "true", "yes"}
OFFLINE = os.getenv("BDF_OFFLINE", "").lower() in {"1", "true", "yes"}
KERNEL_NAME = os.getenv("BDF_NOTEBOOK_KERNEL", "python3")
TIMEOUT = int(os.getenv("BDF_NOTEBOOK_TIMEOUT", "600"))
NOTEBOOK_OPTIONAL_DEPS = {
    "table_parser.ipynb": ("fastnda", "fastnda not installed; skipping NDA notebook coverage."),
}


@pytest.fixture
def _block_kernel_network(request, monkeypatch):
    """Route the notebook kernel's outbound HTTP(S) to a dead proxy under ``--block-cached-sockets``.

    ``pytest-socket`` only patches sockets in the pytest process; the Jupyter
    kernel runs in a separate subprocess and would otherwise re-download on a
    cache miss, silently defeating the guard. The kernel inherits ``os.environ``,
    so pointing ``HTTP(S)_PROXY`` at a dead port makes any cache-miss fetch fail
    loudly while a cache hit (no network) still passes. ``requests``/urllib
    honour these; ZMQ kernel<->client traffic is raw-socket and proxy-agnostic,
    and ``NO_PROXY`` exempts localhost for good measure.

    Args:
        request: The pytest request, used to read ``--block-cached-sockets``.
        monkeypatch: Fixture used to set and auto-restore the proxy env vars.
    """
    if not request.config.getoption("--block-cached-sockets"):
        return
    dead = "http://127.0.0.1:9"
    monkeypatch.setenv("HTTP_PROXY", dead)
    monkeypatch.setenv("HTTPS_PROXY", dead)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,::1,localhost")


@pytest.mark.notebooks
@pytest.mark.slow
@pytest.mark.network
@pytest.mark.parametrize("notebook_path", NOTEBOOKS, ids=lambda p: p.name)
@pytest.mark.usefixtures("_block_kernel_network")
def test_example_notebooks_execute(notebook_path: Path):
    if not NOTEBOOKS:
        pytest.skip("No notebooks found under examples/")
    if SKIP_NOTEBOOKS:
        pytest.skip("BDF_SKIP_NOTEBOOKS is set; skipping notebook execution.")
    if OFFLINE:
        pytest.skip("BDF_OFFLINE is set; skipping notebook execution.")
    optional_dep = NOTEBOOK_OPTIONAL_DEPS.get(notebook_path.name)
    if optional_dep is not None:
        module_name, reason = optional_dep
        if importlib.util.find_spec(module_name) is None:
            pytest.skip(reason)

    nb = nbformat.read(notebook_path, as_version=4)
    client = NotebookClient(
        nb,
        timeout=TIMEOUT,
        kernel_name=KERNEL_NAME,
        resources={"metadata": {"path": str(notebook_path.parent)}},
    )
    client.execute()
