from __future__ import annotations

import os
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")
from nbclient import NotebookClient


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = ROOT / "examples"
NOTEBOOKS = sorted(EXAMPLES_DIR.glob("*.ipynb"))

SKIP_NOTEBOOKS = os.getenv("BDF_SKIP_NOTEBOOKS", "").lower() in {"1", "true", "yes"}
OFFLINE = os.getenv("BDF_OFFLINE", "").lower() in {"1", "true", "yes"}
KERNEL_NAME = os.getenv("BDF_NOTEBOOK_KERNEL", "python3")
TIMEOUT = int(os.getenv("BDF_NOTEBOOK_TIMEOUT", "600"))


@pytest.mark.notebooks
@pytest.mark.slow
@pytest.mark.network
@pytest.mark.parametrize("notebook_path", NOTEBOOKS, ids=lambda p: p.name)
def test_example_notebooks_execute(notebook_path: Path):
    if not NOTEBOOKS:
        pytest.skip("No notebooks found under examples/")
    if SKIP_NOTEBOOKS:
        pytest.skip("BDF_SKIP_NOTEBOOKS is set; skipping notebook execution.")
    if OFFLINE:
        pytest.skip("BDF_OFFLINE is set; skipping notebook execution.")

    nb = nbformat.read(notebook_path, as_version=4)
    client = NotebookClient(
        nb,
        timeout=TIMEOUT,
        kernel_name=KERNEL_NAME,
        resources={"metadata": {"path": str(notebook_path.parent)}},
    )
    client.execute()
