# Changelog
All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - Unreleased
### Breaking
- Dropped Python 3.9 support; now requires Python ≥3.10 (tested through 3.14).
- `read()` gives a `(polars.DataFrame, metadata)` tuple, replacing the old pandas `read(...) -> pandas.DataFrame`.
- New `scan()` gives a `(polars.LazyFrame, metadata)` tuple.
- `load()` removed, use `read()` or `scan()` for all files (BDF or non-BDF).
- `read`/`scan` signature changed:
  - `source` → `path`
  - `registry_path` removed.
  - `include_optional` removed — optional BDF columns are now always kept.
  - `extra_columns` (extra column mapping dict) removed
  - `include_unknown` added, keeps all non-spec columns in the dataframe under their original names (default `False`).
  - `tz` kwarg added for naive datetimes.
- `parse()` is removed — use `read(path, normalize=False, validate=False)`.
- `save()` rewritten on Polars with more files supported, a `validate` kwarg, and a `labels` option (`"preferred" | "machine" | "unchanged"`) replacing the old `human=True/False` toggle. The `.metadata.json` sidecar behavior is unchanged.
- `save()` to JSON previously output NDJSON, which cannot be read by standard JSON parsers. These are now two distinct options, save to ".ndjson" to get newline-delimited JSON.
- Top-level `plugins()` function removed; use `bdf.plugins.list_sources()` instead.
- `ingest()` gets the same kwarg changes as `read`/`scan`/`save`: `include_optional` removed, `include_unknown` added, and `human: bool = False` → `labels: Literal["preferred", "machine", "unchanged"] = "machine"`.
- `ingest` CLI: `--include-optional` removed, `--include-unknown` added, `--labels` option added.
- CLI: `clean` and `plot` no longer take `--assume-bdf`.
- Column spec is now ontology-driven (`bdf.spec.ColumnOntology`, synced from the published BDF ontology release); `bdf.normalize`, `bdf.units`, `bdf.detect`, and `bdf.data_sources` are removed in favor of `bdf.plugins`, `bdf.table_parsers`, `bdf.metadata_parsers`, and `bdf.table_normalizers`.
- `fastnda` install extra renamed to `nda`.
- `Quantity.unit_conversion` renamed to `convert_to`.
- Arbin normalizers (csv, xlsx) map Charge/Discharge Capacity and Energy to the `schedule_*` columns (`Schedule Charging Capacity / Ah`, ...) from ontology 1.3.0, reflecting the operator-defined reset behavior Arbin confirmed; these columns previously landed on the never-resetting test-scoped terms.
- `read()`/`scan()` raise `BDFValidationError` when an elapsed-time column's increments disagree with the recorded timestamps (e.g. milliseconds stored under a seconds header); pass `reconcile_time=True` to repair known unit factors or `validate=False` to load the data as-is.

### Added
- BDF parsers/normalizers for BDF JSON, NDJSON, Arrow/Feather (IPC), XLSX.
- Arbin MITS XLSX parser.
- PyBaMM simulation-output table normalizer (`pybamm` plugin).
- `validate` now checks ontology-defined derived-column consistency.
- Time-scale detection (GH #65): elapsed-time columns are cross-checked against wall-clock increments on read, following the fsck model (detect loudly by default, repair only with the explicit `reconcile_time=True` flag; repairs are recorded under `metadata["time_reconciliation"]`). `validate` reports the same mismatch as a `time_scale` finding.
- Ontology 1.3.0: `schedule_*` capacity/energy terms for schedule-driven accumulators and `step_record_index` (replacing the deprecated `step_index`).
- Ontology release pinning with a bundled snapshot, `BDF_CACHE_DIR` cache override, and a daily auto-sync workflow.
- New optional extras `excel`, `mat`, `mpr`, `yaml` for additional file formats, and an `all` bundle covering all user-facing feature extras.
- Docs: example notebooks now execute live via myst-nb, plus a generated "Supported Plugins" reference page.

### Changed
- I/O layer rebuilt on Polars.
- `repair` and `validate` rebuilt on Polars: `fix_time`, `clean`, and `validate_df` accept polars (eager or lazy) or pandas frames and return matching kinds, with identical results across input types; the CLI no longer round-trips through pandas.
- Dev and docs dependencies moved to PEP 735 dependency-groups; plotting deps moved into a new `plot` extra.
- `save()` now validates via `ColumnOntology.validate_df`, which also warns on non-canonical BDF units.
- `ColumnOntology.load_version()` now fetches and caches an uncached ontology release instead of raising.

### Fixed
- Unix-time conversion is now datetime-resolution-safe: previously assumed nanosecond storage and returned values 1000x too small on pandas builds that yield `[us]`/`[s]`/`[ms]` datetimes.
- `ingest` now lowercases the cell id in per-cell metadata directory paths, stable on case-sensitive filesystems.
- Compound file extensions (e.g. `.bdf.csv.gz`) no longer fail to match a plugin.
- Daylight-saving-time handling in naive-datetime parsing.
- Special characters can be used in units.
- `ohm` and `degC` are accepted as units.
- Deprecated-column redirection (read/load/save) now follows the ontology's `isReplacedBy` link, fixing silent data loss/mislabeling for renamed legacy columns (e.g. `step_capacity_ah` → `step_cumulative_capacity_ah`).
- Excel parser raises on ambiguous `sheet_pattern` matches instead of silently reading only the first matching sheet.
- BDF table normalizer now accepts machine-notation and deprecated on-disk headers, fixing read/validate round-trips of default `save()` artifacts.
- `validate()` now uses plugin detection to decide whether a file is a BDF artifact.
- `.json` artifacts are now valid standalone JSON (a records array via `write_json`); previously `save()` wrote JSON-Lines under the `.json` extension, producing files that failed to parse as JSON outside pandas. Use the new `.ndjson` format for JSON-Lines output.

## [0.1.0] - 2026-02-10
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
