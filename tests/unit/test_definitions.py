from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from bdf._definitions import DefinitionSource
from bdf._errors import BDFDefinitionError, BDFDefinitionWarning


class FakeSource:
    """A DefinitionSource whose download writes data.txt and records each tag it fetches."""

    def __init__(self, tmp_path: Path, bundled: str = "1.3.0") -> None:
        self.bundle = tmp_path / "bundle"
        self.bundle.mkdir()
        (self.bundle / "VERSION").write_text(bundled)
        (self.bundle / "data.txt").write_text(f"bundled {bundled}")
        self.cache = tmp_path / "cache"
        self.fetched: list[str] = []
        self.fail = False
        self.latest = "1.4.0"
        self.down = False
        self.asked = 0
        self.source = DefinitionSource(
            "demo",
            bundled=self.bundle,
            tag_prefix="",
            find_latest=self._find_latest,
            download=self._download,
            open=lambda path: (path / "data.txt").read_text(),
        )

    def _find_latest(self, timeout: float) -> str:
        self.asked += 1
        if self.down:
            raise ConnectionError("down")
        return self.latest

    def _download(self, tag: str, dest: Path, timeout: float) -> None:
        self.fetched.append(tag)
        (dest / "data.txt").write_text(f"fetched {tag}")
        if self.fail:
            raise OSError("download failed")

    def cached(self, version: str, with_version_file: bool = True) -> Path:
        entry = self.cache / f"demo-v{version}"
        entry.mkdir(parents=True)
        (entry / "data.txt").write_text(f"cached {version}")
        if with_version_file:
            (entry / "VERSION").write_text(version)
        return entry


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSource:
    monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path / "cache"))
    return FakeSource(tmp_path)


def test_normalise_ignores_a_leading_v_and_spaces() -> None:
    assert {DefinitionSource.normalise(n) for n in ["v1.1.0", " 1.1.0\n", "1.1.0"]} == {"1.1.0"}
    with pytest.raises(BDFDefinitionError, match="nope"):
        DefinitionSource.normalise("nope")


def test_latest_is_asked_once_and_named_in_normal_form(fake: FakeSource) -> None:
    fake.latest = "v1.4.0"
    assert fake.source.ask_latest_version() == "1.4.0"
    assert fake.source.ask_latest_version() == "1.4.0"
    assert fake.asked == 1
    fake.source.ask_latest_version(refresh=True)
    assert fake.asked == 2


def test_a_failure_to_name_the_latest_version_is_remembered_as_its_message(fake: FakeSource) -> None:
    fake.down = True
    with pytest.raises(BDFDefinitionError, match="demo: upstream could not be reached") as first:
        fake.source.ask_latest_version()
    with pytest.raises(BDFDefinitionError, match="demo: upstream could not be reached") as again:
        fake.source.ask_latest_version()
    # Upstream is asked once. The first error keeps the cause; a later one is a new error, so the
    # remembered failure holds no traceback, and no frame of an earlier caller.
    assert fake.asked == 1
    assert isinstance(first.value.__cause__, ConnectionError)
    assert again.value is not first.value
    assert again.value.__cause__ is None
    # refresh asks again.
    fake.down = False
    assert fake.source.ask_latest_version(refresh=True) == "1.4.0"


def test_a_source_with_no_latest_version_always_needs_a_version(fake: FakeSource) -> None:
    fake.source.find_latest = None
    with pytest.raises(BDFDefinitionError, match="demo has no latest version"):
        fake.source.ask_latest_version()
    # The refresh command makes VERSION a required argument: a usage error, with no request.
    with pytest.raises(SystemExit) as info:
        fake.source.run_cli("demo-update", [])
    assert info.value.code == 2
    assert fake.asked == 0
    assert fake.fetched == []


def test_path_prefers_the_bundle_then_the_cache_then_a_fetch(fake: FakeSource) -> None:
    assert fake.source.get_path("1.3.0") == fake.bundle
    cached = fake.cached("1.1.0")
    assert fake.source.get_path("v1.1.0") == cached
    assert fake.fetched == []

    fetched = fake.source.get_path("1.2.0")
    assert fake.fetched == ["1.2.0"]
    assert (fetched / "VERSION").read_text().strip() == "1.2.0"
    assert fake.source.get_path("1.2.0") == fetched
    assert fake.fetched == ["1.2.0"]


def test_path_without_fetch_fails_for_a_version_that_is_not_local(fake: FakeSource) -> None:
    with pytest.raises(BDFDefinitionError, match="1.2.0"):
        fake.source.get_path("1.2.0", fetch=False)


def test_path_refetches_a_cache_entry_with_no_version_file(fake: FakeSource) -> None:
    fake.cached("1.1.0", with_version_file=False)
    assert (fake.source.get_path("1.1.0") / "data.txt").read_text() == "fetched 1.1.0"


def test_a_failed_fetch_leaves_the_cached_version_unchanged(fake: FakeSource) -> None:
    entry = fake.cached("1.1.0")
    fake.fail = True
    with pytest.raises(BDFDefinitionError, match="1.1.0"):
        fake.source.get_path("1.1.0", refresh=True)
    assert (entry / "data.txt").read_text() == "cached 1.1.0"
    assert [p.name for p in fake.cache.iterdir()] == ["demo-v1.1.0"]


def test_a_failed_fetch_of_a_new_version_caches_nothing(fake: FakeSource) -> None:
    fake.fail = True
    with pytest.raises(BDFDefinitionError):
        fake.source.get_path("1.2.0")
    assert list(fake.cache.iterdir()) == []


def test_write_replaces_the_old_files(fake: FakeSource) -> None:
    (fake.bundle / "stale.txt").write_text("old")
    fake.source.write(fake.bundle, "1.4.0")
    assert sorted(p.name for p in fake.bundle.iterdir()) == ["VERSION", "data.txt"]


def test_write_makes_a_directory_others_can_read(fake: FakeSource, tmp_path: Path) -> None:
    old_umask = os.umask(0o022)
    try:
        dest = fake.source.write(tmp_path / "out", "1.4.0")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o755


def test_get_opens_the_version_on_each_call_and_refresh_fetches_again(fake: FakeSource) -> None:
    fake.cached("1.1.0")
    assert fake.source.get("1.1.0") == "cached 1.1.0"
    (fake.cache / "demo-v1.1.0" / "data.txt").write_text("changed on disk")
    assert fake.source.get("1.1.0") == "changed on disk"
    assert fake.source.get("1.1.0", refresh=True) == "fetched 1.1.0"


def test_get_opened_opens_a_version_once_until_a_write_over_it(fake: FakeSource) -> None:
    fake.cached("1.1.0")
    first = fake.source.get_opened("v1.1.0")
    (fake.cache / "demo-v1.1.0" / "data.txt").write_text("changed on disk")
    # The same object, not opened again: it does not see the change, which get() does.
    assert fake.source.get_opened("1.1.0") is first == "cached 1.1.0"
    assert fake.source.get("1.1.0") == "changed on disk"
    # forget() forgets what upstream said, not the opened versions...
    fake.source.forget()
    assert fake.source.get_opened("1.1.0") is first
    # ...and a write over the directory drops the object, so the next call opens the new files.
    fake.source.get("1.1.0", refresh=True)
    assert fake.source.get_opened("1.1.0") == "fetched 1.1.0"
    # The bundle opens once too.
    assert fake.source.get_opened("1.3.0", fetch=False) is fake.source.get_opened("1.3.0") == "bundled 1.3.0"


def test_a_failed_fetch_is_not_tried_again_until_refresh(fake: FakeSource) -> None:
    fake.fail = True
    with pytest.raises(BDFDefinitionError):
        fake.source.get("1.2.0")
    fake.fail = False
    with pytest.raises(BDFDefinitionError, match="earlier in this process"):
        fake.source.get("1.2.0")
    assert fake.fetched == ["1.2.0"]
    assert fake.source.get("1.2.0", refresh=True) == "fetched 1.2.0"


def test_a_release_cached_after_a_failed_fetch_loads(fake: FakeSource) -> None:
    fake.fail = True
    with pytest.raises(BDFDefinitionError):
        fake.source.get("1.2.0")
    fake.cached("1.2.0")
    assert fake.source.get("1.2.0") == "cached 1.2.0"


def test_get_latest_loads_upstreams_latest_version(fake: FakeSource) -> None:
    assert fake.source.get_latest() == "fetched 1.4.0"


def test_get_latest_falls_back_to_the_newest_local_final_version(fake: FakeSource) -> None:
    fake.down = True
    for version in ["1.9.0", "1.10.0", "1.11.0rc1"]:
        fake.cached(version)
    with pytest.warns(BDFDefinitionWarning, match="could not be reached.*1.10.0.*out of date") as record:
        assert fake.source.get_latest() == "cached 1.10.0"
    assert record[0].filename == __file__


def test_get_latest_falls_back_when_the_latest_version_cannot_be_fetched(fake: FakeSource) -> None:
    fake.fail = True
    with pytest.warns(BDFDefinitionWarning, match="1.4.0"):
        assert fake.source.get_latest() == "bundled 1.3.0"


def test_get_latest_fails_when_there_is_nothing_to_fall_back_to(fake: FakeSource) -> None:
    fake.down = True
    (fake.bundle / "VERSION").write_text("2.0.0rc1")
    with pytest.raises(BDFDefinitionError, match="could not be reached"):
        fake.source.get_latest()
