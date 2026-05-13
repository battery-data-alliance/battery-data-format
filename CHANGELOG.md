# Changelog
All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Changed
- **Breaking:** `step_capacity_ah` and `step_energy_wh` now use a signed convention: positive = net charging, negative = net discharging. Previously the Digatron plugin emitted unsigned magnitudes (always ≥ 0). The formula is `Δcharging − Δdischarging` within each step, consistent with BioLogic, Arbin, and the majority of cycler manufacturers. Files produced by earlier versions will have incorrect signs for discharge steps.
- Digatron plugin: `fixup()` now re-derives `Step Capacity / Ah` and `Step Energy / Wh` from the signed formula above, overriding the raw unsigned `AhStep`/`WhStep` values from the instrument.

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
