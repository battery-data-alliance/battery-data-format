# Changelog
All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- BioLogic `.mpr` (binary) reader (`biologic-mpr` plugin) via the optional `yadg` dependency (`pip install batterydf[mpr]`), including EIS columns and OCV handling. The `.mpt` reader now injects a zero current column for current-free techniques (e.g. OCV). BioLogic `.mpr`/`.mpt` parsing is cross-validated against shared fixtures downloaded on demand from a pinned `yadg` commit.
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
