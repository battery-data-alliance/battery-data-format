from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from bdf.spec import _BDF_LATEST_URL, _BDF_RELEASE_URL_TMPL


class FakeGitHub:
    """Stands in for requests.get: serves the latest ontology and ontology files by version."""

    def __init__(self) -> None:
        self.latest: str | None = None  # the version the latest URI serves; None answers 404
        self.files: dict[str, str] = {}
        self.down = False
        self.urls: list[str] = []

    def __call__(self, url: str, **kwargs: object) -> Mock:
        self.urls.append(url)
        if self.down:
            raise requests.ConnectionError("down")
        if url == _BDF_LATEST_URL and self.latest:
            return Mock(status_code=200, content=ontology_ttl(self.latest).encode())
        for version, text in self.files.items():
            if url == _BDF_RELEASE_URL_TMPL.format(version=version):
                return Mock(status_code=200, content=text.encode())
        answer = Mock(status_code=404)
        answer.raise_for_status.side_effect = requests.HTTPError("404", response=answer)
        return answer


def ontology_ttl(version: str) -> str:
    """Return the smallest ontology that states ``version``."""
    return f"""\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://w3id.org/battery-data-alliance/ontology/battery-data-format> a owl:Ontology ;
    owl:versionInfo "{version}" .
"""


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr("requests.get", fake)
    return fake
