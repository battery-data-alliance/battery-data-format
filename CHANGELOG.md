# Changelog
All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- **Robust fallback column mapping in the normalizer (all plugins benefit).** When a vendor header is not matched by an exact synonym, the normalizer now applies deterministic fallbacks before giving up: (1) **token canonicalization** expands common, unambiguous abbreviations/spellings on both the header and every synonym/alias key (`temp↔temperature`, `chg↔charge`, `dchg↔discharge`, `cap↔capacity`, `volt↔voltage`, `res↔resistance`, …), so cosmetically different headers like `Surface_Temp(degC)` or `Chg Cap (Ah)` resolve; (2) a **unit-dimension guard** rejects any name match whose header unit conflicts with the target quantity's dimension (e.g. an `Aux_Voltage` column can never be accepted as a temperature). Matching stays exact/deterministic — no fuzzy distance — so there are no silent wrong mappings. Ambiguous headers (e.g. bare `cell temperature`, `pressure`) deliberately stay unmapped.
- Shared package aliases in `ingest_aliases.py` for a single unqualified surface/skin thermocouple → `Surface Temperature T1` (`surface_temp`, `surface temperature`, `skin temperature`, …); `cell temperature` is intentionally excluded as ambiguous.
- **`bdf.read(..., keep_unmapped=True)` is now the default.** Vendor columns with no BDF canonical mapping are retained in the output (ordered after the canonical columns) and a `UserWarning` lists them, instead of being silently dropped — making missing aliases auditable. Pass `keep_unmapped=False` to restore canonical-only output.
- **Arbin MITS Pro support** via a new `arbin` data source registering two plugins: `arbin-csv` (Format A — comma-delimited CSV with space-separated, unit-bearing headers, e.g. `Test Time (s)`, `Charge Capacity (Ah)`, `Aux_Temperature_1 (C)`) and `arbin-xlsx` (Format B — `.xlsx` with underscore-separated headers, e.g. `Test_Time(s)`, `Charge_Capacity(Ah)`, `Aux_Temperature(°)_N`). Arbin `Step_Index` (the schedule step id) maps to `Step ID` per the BDF step-quantity convention; the degree-symbol auxiliary temperature headers of Format B are normalised to `Surface Temperature TN / degC`. The `arbin-xlsx` sniffer confirms an Arbin workbook by peeking the header row (openpyxl read-only) so it out-ranks the generic `excel-xlsx` reader only for genuine Arbin files, and it scans every worksheet to select the time-series sheet — real MITS Pro exports place a `Global_Info`/`TEST REPORT` metadata sheet first and the data on a later `Channel_N` sheet. Both plugins re-accumulate `Charging`/`Discharging Capacity` and `Charging`/`Discharging Energy` through any instrument counter resets in `fixup()`: Arbin exports these as test-wide directional accumulators but the test schedule can issue an occasional reset-to-zero at a step boundary, which would otherwise violate the BDF definition that these never reset between steps or cycles. The re-accumulation is a no-op (instrument values preserved bit-for-bit) when no reset is present, and a `UserWarning` is emitted when it fires. Validated end-to-end against a real 131k-row Arbin CSV in the Zenodo validation suite and against multi-sheet `Channel_N` workbooks up to 240k rows.
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
