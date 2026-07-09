# Changelog
All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-09
### Changed
**BREAKING — spec-driven read/normalize stack (#46).** The legacy parsing/normalization/unit modules (`detect.py`, `data_sources/`, `normalize/`, `units/`, `time.py`) were removed and `bdf.read`/`bdf.normalize`/`bdf.detect` now route through the ontology-driven `io`/`plugins`/`table_normalizers` stack. Three breaking changes stack on every `bdf.read` call site:

1. **Return shape:** `bdf.read` returns a `(df, metadata)` tuple instead of a bare DataFrame. `metadata` always carries `"source"` (the resolved plugin id, or `"custom"` for a directly-supplied `Plugin`) plus any fields the plugin's metadata parser extracts (currently `start_time` for BioLogic/BaSyTec/Maccor preambles).
2. **DataFrame library:** the returned frame is polars, not pandas. Use `.to_pandas()` where pandas is required (`bdf.load`/`bdf.save`/`bdf.clean`/`bdf.plot` still operate on pandas).
3. **Laziness:** the default return is a `pl.LazyFrame`; pass `lazy=False` (or call `.collect()`) for an eager DataFrame.

Migration table:

| Before (0.1.x) | Now (0.2.0) |
| --- | --- |
| `df = bdf.read(p)` | `df, meta = bdf.read(p, lazy=False)` |
| ...and downstream pandas code | `df = df.to_pandas()` after the call above |
| `bdf.parse(p)` | `bdf.read(p, normalize=False, validate=False)` |
| `bdf.plugins()` | `bdf.plugins.list_sources()` |
| `bdf.detect(p)` → `SniffResult` | `bdf.detect(p)` → `(plugin_id, Plugin)` |
| `bdf.normalize(df, plugin="...")` | `bdf.normalize(df, normalizer=...)` (polars or pandas in, same type out) |
| plugin ids `biologic-mpt`, `neware-csv`, ... | underscore ids: `biologic_mpt`, `neware_csv`, ... |

`bdf.read` on an existing BDF artifact still validates by default; legacy on-disk labels (deprecated notations such as `test_time_millisecond`) are normalized to current labels on read via ontology-derived synonyms.

Non-breaking:
- Enriched packaging metadata and optional extras; relaxed numpy upper bound and added a numpy2 install extra.
- Improved README with install/quickstart and CLI examples.
- Switched PyPI distribution name from `bdf` to `batterydf` (import/CLI remain `bdf`).

### Added
- `tz` parameter on `bdf.read`/`bdf.normalize`/`TableNormalizer.normalize`: vendor formats without an embedded UTC offset can be localized to an explicit IANA timezone; leaving the default UTC emits a `UserWarning` when a naive datetime format is in play. DST-ambiguous times resolve to the earliest instant, non-existent times become null. (#50)
- PyBaMM table normalizer (`NORMALIZERS["pybamm"]`) for frames exported via `pybamm.Solution.get_data_dict()`, converting PyBaMM's discharge-positive convention to BDF's charge-positive one; real-solution integration test behind the `pybamm-test` extra. (#49)
- `spec.load_version` fetches and caches an ontology release when no local snapshot exists instead of raising. (#46)
- Auto-generated "Supported Plugins" docs page built from `bdf.plugins.PLUGINS` on every docs build; example notebooks execute live via myst-nb. (#46)
- CI pipeline with lint/type/tests/docs and build/twine checks.
- Sphinx docs with pydata theme and converted notebook examples.
- Unit tests for IO, registry, validation, repair, CLI, and raw conversion.
- CLI/core alignment (`save_jsonld`, metadata helpers).
- Community files: CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.
- Release workflow for TestPyPI/PyPI publication via GitHub Actions.

### Fixed
- Unix-time conversion is now datetime-resolution-safe: `bdf.time.parse_unix_time` computed epoch seconds via `astype("int64") / 1e9`, which assumed nanosecond storage and returned values 1000× too small on pandas builds that yield `[us]`/`[s]`/`[ms]` datetimes (e.g. newer Python/pandas in CI). Now uses timedelta arithmetic (`(dt - epoch).dt.total_seconds()`), correct for any resolution.
- `bdf.ingest` now lowercases the cell id when creating the per-cell metadata directory, so generated paths are stable on case-sensitive filesystems (the cell `metadata.jsonld` was previously written to a differently-cased directory on Linux).
