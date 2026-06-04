# Changelog
All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- `bdf.derive()` / `bdf.read(..., derive=True)`: compute BDF derived quantities from the three required base columns (`Test Time / s`, `Voltage / V`, `Current / A`) by trapezoidal integration — `Power`, charging/discharging/cumulative/net `Capacity` and `Energy`, and (when a `Step ID` column is present) per-step `Step Capacity`/`Step Energy`/`Step Time`/`Step Count`/`Step Index`. Supports `fill_missing` (populate absent columns) and `validate=True` (warn when vendor-supplied values diverge from the recomputation).
- First-class **term-rule validation**: `bdf.validate.check_term_rules()` (and `validate_df`, which now reports `rule_violations`) checks that data adheres to each quantity's definition — accumulators are monotonically non-decreasing, magnitude/throughput columns are non-negative, and signed `Net` integrals never exceed the corresponding `Cumulative` throughput. Violations are warning-level and surfaced in the report (they don't fail validation, since instrument quirks like counter resets can legitimately trip them) so sign/scaling/reset bugs become auditable.
- `cumulative_capacity_ah` / `cumulative_energy_wh` now explicitly defined as throughput (`charging + discharging`), always monotonically non-decreasing. Digatron plugin now computes these correctly (previously emitted `charging - discharging`, the same as net).
- `net_capacity_ah` / `net_energy_wh` now explicitly defined as the running integral of signed current/power from test start (`charging - discharging`); can be negative. Equivalent to BioLogic Q-Q0. Digatron's `AhBal`/`WhBal` (instrument-internal balance values) replaced by the correct formula.
- `charging_capacity_ah`, `discharging_capacity_ah`, `charging_energy_wh`, `discharging_energy_wh` definitions clarified: these accumulate from test start and never reset between steps or cycles — distinct from step-level accumulators used by some instruments (e.g. BioLogic EC-Lab).
- `cycle_count` (`Cycle Count / 1`) starting value clarified: any instrument-defined starting value (0, 1, or user-configured) is valid and must be preserved by converters. Cycle 0 typically represents pre-cycling or conditioning steps.
- New column `step_id` (`Step ID / 1`): the step identifier from the test program schedule. Maps to Arbin `Step_Index`, Neware `Step_ID`, Digatron `Step`, BioLogic `Ns`. Values are instrument-defined and may be non-contiguous or repeating across cycles.
- New column `step_type` (`Step Type / 1`): string label for the step's operational mode (e.g. `CC_CHG`, `CC_DCH`, `CV_CHG`, `REST`, `OCV`). Controlled vocabulary is not yet standardised; values are preserved as reported by the instrument.

### Changed
- **Breaking:** `step_capacity_ah` and `step_energy_wh` now use a signed convention: positive = net charging, negative = net discharging. Previously the Digatron plugin emitted unsigned magnitudes (always ≥ 0). The formula is `Δcharging − Δdischarging` within each step, consistent with BioLogic, Arbin, and the majority of cycler manufacturers. Files produced by earlier versions will have incorrect signs for discharge steps.
- Digatron plugin: `fixup()` now re-derives `Step Capacity / Ah` and `Step Energy / Wh` from the signed formula above, overriding the raw unsigned `AhStep`/`WhStep` values from the instrument.
- **Breaking:** All plugins that previously mapped a schedule step identifier to `Step Index / 1` or `Step Count / 1` now map it to `Step ID / 1`. Affected: Digatron, Neware NDA, Neware CSV, LANDT CSV, LANDT TXT, Novonix. The output column name changes from `Step Index / 1` or `Step Count / 1` to `Step ID / 1` for these vendors.
- **Breaking:** `step_count` (`Step Count / 1`) no longer carries any vendor synonyms. It is defined as a monotonically increasing sequential counter (never exported raw by cyclers; always derived) and will not be auto-populated from vendor files. Use `step_id` for the schedule step identifier.
- `step_index` (`Step Index / 1`) synonym `step-index` removed to avoid collision with Arbin's `Step_Index` (which is a schedule step ID, now routed to `step_id`).
- Digatron plugin: `Status` column now mapped to `Step Type / 1`.
- BioLogic plugin: `Ns` (number of sequences) now mapped to `Step ID / 1`.
- Neware CSV: `Cycle` now correctly mapped to `Cycle Count / 1` (was non-canonical `"Cycle Index"`); `Record` to `Record Index / 1`.
- LANDT CSV/TXT: `cycle_index`/`cycle` now correctly mapped to `Cycle Count / 1` (was non-canonical `"Cycle Index"`).

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
