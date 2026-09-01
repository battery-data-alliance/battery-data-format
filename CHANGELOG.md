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
- `save()` rewritten on Polars with more files supported, a `validate` kwarg, and a `labels` option (`"preferred" | "machine" | "unchanged"`) replacing the old `human=True/False` toggle.
- `save()` to JSON previously output NDJSON, which cannot be read by standard JSON parsers. These are now two distinct options, save to ".ndjson" to get newline-delimited JSON.
- Top-level `plugins()` function removed; use `bdf.plugins.list_sources()` instead.
- `ingest()` gets the same kwarg changes as `read`/`scan`/`save`: `include_optional` removed, `include_unknown` added, and `human: bool = False` → `labels: Literal["preferred", "machine", "unchanged"] = "machine"`.
- `ingest` CLI: `--include-optional` removed, `--include-unknown` added, `--labels` option added.
- CLI: `clean` and `plot` no longer take `--assume-bdf`.
- Column spec is now ontology-driven (`bdf.spec.ColumnOntology`, synced from the published BDF ontology release); `bdf.normalize`, `bdf.units`, `bdf.detect`, and `bdf.data_sources` are removed in favor of `bdf.plugins`, `bdf.table_parsers`, `bdf.metadata_parsers`, and `bdf.table_normalizers`.
- `fastnda` install extra renamed to `nda`.
- Module paths `bdf.validate`, `bdf.templates`, and `bdf.ingest` renamed to private submodules; the `validate()`, `templates()`, and `ingest()` functions are unchanged and remain importable from the `bdf` namespace. Fixes module/function name collisions.
- `Quantity.unit_conversion` renamed to `convert_to`.
- Arbin normalizers (csv, xlsx) map Charge/Discharge Capacity and Energy to the `schedule_*` columns (`Schedule Charging Capacity / Ah`, ...) from ontology 1.3.0, reflecting the operator-defined reset behavior Arbin confirmed; these columns previously landed on the never-resetting test-scoped terms.
- `read()`/`scan()` raise `BDFValidationError` when an elapsed-time column's increments disagree with the recorded timestamps (e.g. milliseconds stored under a seconds header); pass `reconcile_time=True` to repair known unit factors or `validate=False` to load the data as-is.
- `bdf.read` and `bdf.scan` return a typed `Metadata` model as the second tuple element, replacing the plain `dict`. Read a field by attribute (e.g. `metadata.bdf.source`, `metadata.battinfo_test.test.started_at`) rather than by key.
- `bdf.save` takes a typed `Metadata` model in `metadata=`, replacing the plain `dict`. Pass `Metadata(...)`, the model `bdf.read` returns, or `Metadata.model_validate(mapping)` for a mapping an earlier version wrote.

### Added
- BDF parsers/normalizers for BDF JSON, NDJSON, Arrow/Feather (IPC), XLSX.
- Arbin MITS XLSX parser.
- Arbin `.res` parser (Access/MDB, via pyodbc on Windows or MDB Tools elsewhere; `arbin_res` extra). Contributed by @Abbta.
- BioLogic `.mpr` binary parser via yadg (`mpr` extra), including the EIS quantities; adds a `reverse_sign` option to normalizer synonyms.
- PyBaMM simulation-output table normalizer (`pybamm` plugin).
- `validate` now checks ontology-defined derived-column consistency.
- Time-scale detection (GH #65): elapsed-time columns are cross-checked against wall-clock increments on read, following the fsck model (detect loudly by default, repair only with the explicit `reconcile_time=True` flag; repairs are recorded under `metadata["time_reconciliation"]`). `validate` reports the same mismatch as a `time_scale` finding.
- Ontology 1.3.0: `schedule_*` capacity/energy terms for schedule-driven accumulators and `step_record_index` (replacing the deprecated `step_index`).
- Ontology release pinning with a bundled snapshot, `BDF_CACHE_DIR` cache override, and a daily auto-sync workflow.
- New optional extras `excel`, `mat`, `mpr`, `yaml` for additional file formats, and an `all` bundle covering all user-facing feature extras.
- `save()` streams a `LazyFrame` straight to disk via the polars `sink_*` writers (csv, parquet, ipc, ndjson), so a scanned table no longer has to fit in memory to be written; json and xlsx still collect.
- CLI piping: `convert` and `validate` accept `-` to read from stdin, and `convert --to -` writes BDF CSV to stdout; status messages go to stderr. Exit codes: 0 valid, 1 invalid, 2 unreadable.
- Docs: example notebooks now execute live via myst-nb, plus a generated "Supported Plugins" reference page.
- `read()` and `scan()` restore the `.metadata.json` sidecar a prior `save()` wrote, in place of the plugin parser's metadata.
- `day_month_order` keyword-only override on `bdf.read`, `bdf.scan`, `bdf.normalize`, `TableParser.read`, and `TableNormalizer.normalize`, for a vendor datetime format that leaves a numeric day and month ambiguous. `"day_first"` reads such a date day then month, `"month_first"` reads it month then day, and the default `None` leaves every declared format exactly as the plugin states it. A year-first format (e.g. `%Y-%m-%d`) or a month-name format (e.g. `%d-%b-%y`) is never affected.
- Every sidecar `save()` writes is stamped with its writer's identity under `metadata.bdf` (GH #106): `bdf_version` (the batterydf package), `ontology_version` (the pinned BDF ontology release), and `battinfo_ref` (the upstream commit of the bundled BattINFO schemas). The stamps overwrite on each save, so they always name the file's writer; an empty `Metadata` still writes no sidecar.
- `Metadata` gains a sixth record, `battinfo_dataset` (`bdf.BattinfoDataset`), mirroring BattINFO's dataset schema: distributions (raw and processed files with checksums and formats), identifiers, and links to the cell and test that produced the data. Nothing fills it automatically yet; it is the documented place for file-level provenance (GH #92, #105). Like the other records it mirrors a pre-1.0 upstream schema and may evolve with it.
- Network test that fails when the bundled BattINFO schemas differ from upstream `main`; the pinned-commit test could not catch a stale bundle, because a refetch at a commit reproduces it however far upstream moves. The comparison reads the parsed content of the eight managed schema files, so an upstream commit that reformats one of them or that leaves `assets/schemas/` alone keeps it green. `scripts/update_battinfo.py --check` reports the same comparison and exits non-zero when the bundle is stale, and the script now resolves a branch or tag to a commit before it stamps `VERSION`.

### Changed
- I/O layer rebuilt on Polars.
- `repair` and `validate` rebuilt on Polars: `fix_time`, `clean`, and `validate_df` accept polars (eager or lazy) or pandas frames and return matching kinds, with identical results across input types; the CLI no longer round-trips through pandas.
- Dev and docs dependencies moved to PEP 735 dependency-groups; plotting deps moved into a new `plot` extra.
- `save()` now validates via `ColumnOntology.validate_df`, which also warns on non-canonical BDF units.
- `ColumnOntology.load_version()` now fetches and caches an uncached ontology release instead of raising.
- `NDAParser` renamed to `NdaParser`; its magic-byte check now recognizes the `.ndax` zip container.
- Package import is lazy: `import bdf` completes in ~0.15 s without loading pandas/polars/scipy; heavy dependencies import on first use, making CLI startup near-instant.
- `save()` names the metadata sidecar by replacing the final extension (`x.bdf.parquet` -> `x.bdf.metadata.json`) instead of appending to the full filename.
- A metadata sidecar that exists and cannot be restored now raises the new `BDFMetadataError` instead of reading as an empty `Metadata`: a document that does not decode as UTF-8, one that does not parse as JSON, and one holding a JSON value other than an object. This applies to the reserved `.metadata.json` sidecar and to a plugin-declared sidecar alike. An empty `Metadata` now states that no sidecar exists, so the documented `read()` then `save()` round trip can no longer write a degraded object over a sidecar the read failed on. The naive-timestamp `UserWarning` is unchanged.
- `save()` no longer carries a metadata sidecar from one save to the next. A `save()` that states no `metadata=` beside an existing `.metadata.json` sidecar raises `FileExistsError`, because the sidecar describes the data the previous save wrote; pass `metadata=` to keep or update it, `metadata=Metadata()` to clear it, or save to a different path. An empty `Metadata` now deletes the sidecar, so a sidecar exists exactly where the artifact has metadata.
- `read()` and `scan()` now cut a preamble metadata source where the table begins, so `raw` carries the preamble alone, with neither the header row nor a data row. A caller who drives a metadata parser directly (bypassing `read`/`scan`) states no boundary and keeps the whole decoded head, as before. Where the table parser cannot locate the header row, the boundary is unknown and `raw` keeps the whole decoded head. `raw` is `None` where a file's header row is its first line.

### Fixed
- `arbin_res` extra is now part of the `all` bundle, and its mdbtools backend supports Python 3.10 (polars-access-mdbtools 0.1.3).
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
