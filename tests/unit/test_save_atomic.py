"""Regression coverage for crash-safe BDF output writing (GH #104)."""

import os
import stat
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from bdf import Metadata, io


@pytest.fixture
def data():
    return pl.DataFrame(
        {
            "Test Time / s": [0.0, 1.0],
            "Voltage / V": [3.7, 3.6],
            "Current / A": [0.1, 0.1],
        }
    )


@pytest.mark.parametrize("suffix", [".csv", ".csv.gz", ".parquet", ".xlsx"])
@pytest.mark.parametrize("exists", [False, True])
def test_failed_writer_preserves_destination(tmp_path, monkeypatch, data, suffix, exists):
    path = tmp_path / ("case.bdf" + suffix)
    if exists:
        path.write_bytes(b"old-good-data")
    spec = io._FORMATS[io._detect_format(path)]
    owner = pl.LazyFrame if spec.sink else pl.DataFrame

    def failing_writer(self, target, **options):
        if isinstance(target, Path):
            target.write_bytes(b"partial")
        else:
            target.write(b"partial")
        raise RuntimeError("injected writer failure")

    monkeypatch.setattr(owner, spec.sink or spec.write, failing_writer)
    with pytest.raises(RuntimeError, match="injected writer failure"):
        io.save(data.lazy(), path)
    assert path.exists() == exists
    if exists:
        assert path.read_bytes() == b"old-good-data"
    assert set(tmp_path.iterdir()) == ({path} if exists else set())


def test_replace_failure_retains_old_target(tmp_path, monkeypatch, data):
    path = tmp_path / "case.bdf.csv"
    path.write_bytes(b"existing")

    def failing_replace(src, dst):
        raise PermissionError("injected replace failure")

    monkeypatch.setattr(io.os, "replace", failing_replace)
    with pytest.raises(PermissionError, match="injected replace failure"):
        io.save(data, path)
    assert path.read_bytes() == b"existing"
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission behavior")
def test_existing_file_permissions_retained(tmp_path, data):
    path = tmp_path / "case.bdf.csv"
    path.write_bytes(b"old")
    path.chmod(0o640)
    io.save(data, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_sidecar_kept_on_writer_failure(tmp_path, monkeypatch, data):
    path = tmp_path / "case.bdf.csv"
    meta = Metadata()
    meta.battinfo_test.test.name = "original"
    io.save(data, path, metadata=meta)
    data_before = path.read_bytes()
    sidecar = path.with_suffix(".metadata.json")
    sidecar_before = sidecar.read_bytes()

    def failing_sink(self, target, **kwargs):
        if isinstance(target, Path):
            target.write_bytes(b"partial")
        else:
            target.write(b"partial")
        raise RuntimeError("failed sink")

    monkeypatch.setattr(pl.LazyFrame, "sink_csv", failing_sink)
    edited = Metadata()
    edited.battinfo_test.test.name = "changed"
    with pytest.raises(RuntimeError, match="failed sink"):
        io.save(data.lazy(), path, metadata=edited)
    assert path.read_bytes() == data_before
    assert sidecar.read_bytes() == sidecar_before
    assert set(tmp_path.iterdir()) == {path, sidecar}


@pytest.mark.parametrize("suffix", [".csv", ".csv.gz", ".parquet", ".ndjson", ".xlsx"])
@pytest.mark.parametrize("lazy", [False, True])
def test_successful_roundtrip(tmp_path, data, suffix, lazy):
    path = tmp_path / ("case.bdf" + suffix)
    io.save(data.lazy() if lazy else data, path)
    readback, _ = io.read(path)
    assert_frame_equal(data, readback)
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation is privilege-dependent on Windows")
@pytest.mark.parametrize("suffix", [".csv", ".csv.gz"])
def test_save_preserves_symlink_destination(tmp_path, data, suffix):
    real = tmp_path / "physical-data-file"
    alias = tmp_path / ("linked.bdf" + suffix)
    real.write_bytes(b"old")
    alias.symlink_to(real)
    io.save(data.lazy(), alias)
    assert alias.is_symlink()
    assert real.read_bytes() != b"old"
    loaded, _ = io.read(alias)
    assert_frame_equal(data, loaded)
    assert set(tmp_path.iterdir()) == {alias, real}


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation is privilege-dependent on Windows")
def test_save_symlink_failure_preserves_link_and_target(tmp_path, data, monkeypatch):
    real = tmp_path / "physical-data-file"
    alias = tmp_path / "linked.bdf.csv"
    real.write_bytes(b"old-good")
    alias.symlink_to(real)

    def failed(self, target, **kwargs):
        if isinstance(target, Path):
            target.write_bytes(b"partial")
        else:
            target.write(b"partial")
        raise RuntimeError("simulated")

    monkeypatch.setattr(pl.LazyFrame, "sink_csv", failed)
    with pytest.raises(RuntimeError, match="simulated"):
        io.save(data.lazy(), alias)
    assert alias.is_symlink()
    assert real.read_bytes() == b"old-good"
    assert set(tmp_path.iterdir()) == {alias, real}


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and umask semantics")
@pytest.mark.parametrize("suffix", [".csv", ".csv.gz", ".parquet"])
def test_new_artifact_respects_standard_creation_mode(tmp_path, data, suffix):
    control = tmp_path / "control.txt"
    control.write_bytes(b"test")
    path = tmp_path / ("new.bdf" + suffix)
    io.save(data.lazy(), path)
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(control.stat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and umask semantics")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_staging_permissions_not_broader_than_old_file(tmp_path, monkeypatch, data, mode):
    path = tmp_path / "secure.bdf.csv"
    path.write_bytes(b"original")
    path.chmod(mode)
    original_sink = pl.LazyFrame.sink_csv
    observed = []

    def checking_sink(self, target, **opts):
        assert isinstance(target, Path)
        temp_mode = stat.S_IMODE(target.stat().st_mode)
        observed.append(temp_mode)
        assert temp_mode & ~mode == 0
        return original_sink(self, target, **opts)

    monkeypatch.setattr(pl.LazyFrame, "sink_csv", checking_sink)
    io.save(data.lazy(), path)
    assert observed
    assert stat.S_IMODE(path.stat().st_mode) == mode


@pytest.mark.skipif(os.name == "nt", reason="Test assumes POSIX filename length limits")
def test_long_valid_filename_does_not_break_staging(tmp_path, data):
    path = tmp_path / ("x" * 235 + ".csv")
    path.write_bytes(b"previous-valid-artifact")
    io.save(data, path)
    loaded, _ = io.read(path)
    assert_frame_equal(data, loaded)
    assert set(tmp_path.iterdir()) == {path}


def test_compressed_writer_close_failure_preserves_destination(tmp_path, monkeypatch, data):
    path = tmp_path / "case.bdf.csv.gz"
    path.write_bytes(b"previous-valid-artifact")
    closed = []

    class FailingClose:
        def write(self, content):
            return len(content)

        def close(self):
            closed.append(True)
            raise OSError("injected close failure")

    def successful_sink(self, target, **opts):
        target.write(b"staged-data")

    monkeypatch.setattr(io, "open_compressed", lambda path: FailingClose())
    monkeypatch.setattr(pl.LazyFrame, "sink_csv", successful_sink)

    with pytest.raises(OSError, match="injected close failure"):
        io.save(data.lazy(), path)

    assert closed == [True]
    assert path.read_bytes() == b"previous-valid-artifact"
    assert set(tmp_path.iterdir()) == {path}
