"""Helpers for BattINFO tests: release directories, the cache, and a fake GitHub for the download.

The ``battinfo_cache`` fixture is in ``conftest.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from bdf.battinfo import BATTINFO_SOURCE
from bdf.data import BATTINFO_BUNDLE

# Read at import, before any test points the source at another directory.
BUNDLED = BATTINFO_BUNDLE

RAW_URL = "https://raw.githubusercontent.com/BIG-MAP/BattINFO/{tag}/assets/schemas/{path}"


def bundled_docs() -> dict[str, dict[str, Any]]:
    """Return the documents of the packaged release, keyed by their path under ``schemas/``."""
    root = BUNDLED / "schemas"
    return {p.relative_to(root).as_posix(): json.loads(p.read_text()) for p in sorted(root.rglob("*.schema.json"))}


def release_docs(field: str) -> dict[str, dict[str, Any]]:
    """Return a release whose ``test`` record is closed and holds one integer ``field``."""
    record = {"type": "object", "additionalProperties": False, "properties": {field: {"type": "integer"}}}
    doc = {"$id": "https://example.test/test.schema.json", "properties": {"test": record}}
    return {"test.schema.json": doc}


def write_release(root: Path, version: str, docs: dict[str, dict[str, Any]]) -> Path:
    """Write the release directory ``root`` (``VERSION`` and ``schemas/``) from ``docs``."""
    root.mkdir(parents=True)
    (root / "VERSION").write_text(f"{version}\n")
    for path, doc in docs.items():
        (root / "schemas" / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "schemas" / path).write_text(json.dumps(doc))
    return root


CACHE_DIR = "cache"
"""The cache directory under ``tmp_path`` that the ``battinfo_cache`` fixture uses."""


def cache_release(tmp_path: Path, version: str, docs: dict[str, dict[str, Any]]) -> None:
    """Put a release in the cache that ``battinfo_cache`` uses."""
    write_release(tmp_path / CACHE_DIR / f"battinfo-v{version}", version, docs)


def point_bundle_at(monkeypatch: pytest.MonkeyPatch, root: Path, version: str, docs: dict[str, dict[str, Any]]) -> None:
    """Make ``docs`` the bundled release: the source's bundle, which ``BATTINFO_SCHEMA`` opens."""
    monkeypatch.setattr(BATTINFO_SOURCE, "bundled", write_release(root, version, docs))


def serve_raw_github(monkeypatch: pytest.MonkeyPatch, get: Callable[..., Any]) -> None:
    """Answer the BattINFO download's requests with ``get(url, **kwargs)``, such as a FakeRawGitHub.

    The download makes its requests through one ``requests.Session``, so ``Session.get`` is replaced.
    """
    monkeypatch.setattr(requests.Session, "get", lambda _session, url, **kwargs: get(url, **kwargs))


class FakeRawGitHub:
    """Stands in for the download's ``get``: serves the schema documents of releases by tag and path."""

    def __init__(self, docs: dict[str, dict[str, Any]] | None = None, tag: str = "v0.9.0") -> None:
        self.docs = docs if docs is not None else bundled_docs()
        self.tag = tag
        self.down = False
        self.urls: list[str] = []
        self.timeouts: list[float | None] = []

    def __call__(self, url: str, **kwargs: Any) -> Mock:
        self.urls.append(url)
        self.timeouts.append(kwargs.get("timeout"))
        if self.down:
            raise requests.ConnectionError("down")
        for path, doc in self.docs.items():
            if url == RAW_URL.format(tag=self.tag, path=path):
                return Mock(status_code=200, content=json.dumps(doc).encode())
        answer = Mock(status_code=404)
        answer.raise_for_status.side_effect = requests.HTTPError("404", response=answer)
        return answer
