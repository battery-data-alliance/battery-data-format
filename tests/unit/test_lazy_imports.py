from __future__ import annotations

import subprocess
import sys

import bdf


def test_all_public_names_resolve() -> None:
    for name in bdf.__all__:
        assert getattr(bdf, name) is not None


def test_dir_lists_public_names() -> None:
    assert set(bdf.__all__) <= set(dir(bdf))


def test_lazy_attrs_are_public() -> None:
    assert set(bdf._LAZY_ATTRS) <= set(bdf.__all__)


def test_lazy_attribute_is_cached() -> None:
    vars(bdf).pop("read", None)
    assert bdf.read is not None
    assert "read" in vars(bdf)


def test_import_bdf_does_not_load_heavy_dependencies() -> None:
    """Check if bdf import on fresh interpreter imports heavy dependencies."""
    import_code = (
        "import sys, bdf\n"
        "HEAVY_DEPS = ('pandas', 'polars', 'matplotlib', 'rdflib', 'requests', 'scipy', 'plotly')\n"
        "print([name for name in HEAVY_DEPS if name in sys.modules])\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            import_code,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"
