# tests/conftest.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent
DATA = HERE / "data"


def pytest_addoption(parser):
    """Register the ``--block-cached-sockets`` flag.

    Args:
        parser: The pytest argument parser.
    """
    parser.addoption(
        "--block-cached-sockets",
        action="store_true",
        default=False,
        help=(
            "Keep sockets blocked for cache-backed ``network`` tests. Used on CI "
            "after the warm cache restores: a cache miss then fails loudly with a "
            "SocketBlockedError instead of silently re-downloading. Tests marked "
            "``live_network`` still get a socket."
        ),
    )


def pytest_collection_modifyitems(config, items):
    """Re-enable sockets for network-marked items.

    ``--disable-socket`` (set in ``addopts``) blocks all socket access by
    default. ``network`` tests legitimately touch the network, so they normally
    get an ``enable_socket`` marker. With ``--block-cached-sockets`` (CI, post
    warm-cache):

    * ``live_network`` tests always get a real socket.
    * ``notebooks`` tests get ``allow_hosts`` limited to localhost so the
      Jupyter kernel's socket pair / ZMQ channels work, while a remote (cache
      miss) fetch is still blocked and fails loudly.
    * other cache-backed ``network`` tests stay fully blocked, so a cache miss
      raises ``SocketBlockedError`` instead of silently re-downloading.

    Args:
        config: The pytest config object.
        items: The collected test items, mutated in place.
    """
    block_cached = config.getoption("--block-cached-sockets")
    for item in items:
        if item.get_closest_marker("live_network"):
            item.add_marker(pytest.mark.enable_socket)
            continue
        if not item.get_closest_marker("network"):
            continue
        if not block_cached:
            item.add_marker(pytest.mark.enable_socket)
        elif item.get_closest_marker("notebooks"):
            item.add_marker(pytest.mark.allow_hosts(["127.0.0.1", "::1"]))


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


@pytest.fixture(scope="session")
def tiny_files(data_dir: Path):
    """Minimal valid tiny files for each plugin (keep <1KB)."""
    return {
        "biologic": data_dir / "tiny_biologic.mpt",
        "neware": data_dir / "tiny_neware.csv",
        "landt_csv": data_dir / "tiny_landt.csv",
        "basytec": data_dir / "tiny_basytec.txt",
        "digatron": data_dir / "tiny_digatron.csv",
    }


@pytest.fixture(scope="session")
def minimal_registry(data_dir: Path) -> dict:
    with open(data_dir / "minimal_registry.jsonld", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def offline_registry_url() -> str:
    """Fallback to the minimal local registry (used by some unit tests)."""
    return "file://minimal"


@pytest.fixture(scope="session")
def pint_ureg():
    try:
        import pint

        return pint.UnitRegistry()
    except Exception:
        pytest.skip("pint not available")
