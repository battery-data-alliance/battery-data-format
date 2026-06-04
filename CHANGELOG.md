# Changelog
All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- CI pipeline with lint/type/tests/docs and build/twine checks.
- Sphinx docs with pydata theme and converted notebook examples.
- Unit tests for IO, registry, validation, repair, CLI, and raw conversion.
- CLI/core alignment (`save_jsonld`, metadata helpers).
- Community files: CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.
- Release workflow for TestPyPI/PyPI publication via GitHub Actions.

### Changed
- Enriched packaging metadata and optional extras.
- Improved README with install/quickstart and CLI examples.
- Relaxed numpy upper bound and added a numpy2 install extra.
- Switched PyPI distribution name from `bdf` to `batterydf` (import/CLI remain `bdf`).

### Fixed
- Unix-time conversion is now datetime-resolution-safe: `bdf.time.parse_unix_time` computed epoch seconds via `astype("int64") / 1e9`, which assumed nanosecond storage and returned values 1000× too small on pandas builds that yield `[us]`/`[s]`/`[ms]` datetimes (e.g. newer Python/pandas in CI). Now uses timedelta arithmetic (`(dt - epoch).dt.total_seconds()`), correct for any resolution.
- `bdf.ingest` now lowercases the cell id when creating the per-cell metadata directory, so generated paths are stable on case-sensitive filesystems (the cell `metadata.jsonld` was previously written to a differently-cased directory on Linux).
