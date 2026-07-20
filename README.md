# Battery Data Format (.bdf)

## Why introduce an industry standard format for battery data?

It is well known that organizing, cleaning, and preparing battery data for analytics takes significant time and effort, creating a high barrier to leveraging advances in battery modeling for battery development cycles.

[A 2024 Forrester study](https://www.monolithai.com/ev-battery-validation-ai-study) surveyed 165 decision-makers in the automotive industry responsible for EV battery testing, validation, and development in the US and Europe. Among the respondents, 57% cited deciphering complex relationships in vast, multiparameter datasets as a significant barrier to battery validation, and 61% estimated months to years of time savings from AI-powered cell characterization testing that leverages standardized data sets.

## Goal of launching the BDF

The **Battery Data Format (BDF)** provides a standard structure for data generated in battery labs, offered to the community by the **Battery Data Alliance**, a Linux Foundation Energy project. It is our hope that adoption of the BDF will empower the battery science community to leverage advances in open-source battery models.

Developed with input from leading scientists and engineers, the BDF addresses two main challenges:

- **Data Consistency**: With a common format, labs and cycler brands can eliminate the inconsistencies in data structure that arise with each software update.
- **Model Compatibility**: A unified format means battery model developers can easily adapt their models to accept BDF data, making it possible for scientists to experiment with multiple models without custom coding each time.

## Initial Scope of the BDF

- The initial scope is intended to facilitate use and comparison of cycler time-series data.  
- The BDF provides a fixed table schema for time-series battery data, which is supplemented with a machine-readable application ontology for integration with the Semantic Web.  
- The BDF application ontology is defined as an extension of the BattINFO domain ontology, which provides interoperability within the broader field of battery data. 
- An immediate next step will be launching a parallel format for storing metadata for the BDF.
- Future development will focus on formats for other types of lab data such as impedance data.

## Defining the BDF for Cycler Time-Series Data

1. **Each file contains time-series data for one and only one cell.**  
   - Multiple files can be provided for the same cell.

### Self-describing data

A BDF column header is not just a text label — it is a globally unique identifier that machines can look up. Every quantity in the tables below links to a term in the [BDF application ontology](https://github.com/battery-data-alliance/battery-data-format-ontology), which resolves to its full machine-readable definition: physical meaning, canonical unit, mathematical relationship to other quantities, and conformance level (required / recommended / optional). The vocabulary extends the [BattINFO](https://github.com/BIG-MAP/BattINFO) battery domain ontology and is built on established web standards: [RDF](https://www.w3.org/RDF/)/[OWL](https://www.w3.org/OWL/) for formal semantics, [SKOS](https://www.w3.org/2004/02/skos/) for labels and vocabulary mapping, [schema.org](https://schema.org/) annotations for search-engine discoverability, [QUDT](https://qudt.org/) and UCUM for units, [PROV-O](https://www.w3.org/TR/prov-o/) for how derived quantities are computed, and [SOSA](https://www.w3.org/TR/vocab-ssn/)/[CSVW](https://www.w3.org/TR/tabular-data-primer/) for observations and tabular structure.

What that buys you in practice:

- **No vendor ambiguity** — "capacity" from different cyclers maps to explicitly distinct terms (charging, discharging, net, cumulative) with formal definitions.
- **Semantic web-native** — BDF files can be lifted to linked data, indexed by search engines via the schema.org annotations, and integrated with other EMMO-aligned resources.
- **Agentic & automated workflows** — software agents and LLM-based tools can resolve a column's meaning, units, and relationships at runtime, enabling automated validation, unit conversion, and cross-dataset harmonization without human interpretation.
- **No documentation drift** — the tables below, and this package's validation logic, are generated from the same ontology release (the bundled snapshot), so documentation, code, and semantics cannot disagree.

2. **Required quantities**  

<!-- BEGIN GENERATED: bdf-terms-required -->
<!-- Generated from BDF ontology 1.2.0 by scripts/generate_docs.py - do not edit by hand. -->
| Preferred Label | Machine-readable name | IRI | Description |
|---|---|---|---|
| Current / A | `current_ampere` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere](https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere) | Instantaneous current flowing through the test object, in ampere. Sign convention: positive current charges the test object (current flows into it) and negative current discharges it; the charging and discharging capacity and energy quantities are defined by this convention. |
| Test Time / s | `test_time_second` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second) | Elapsed time since the start of the test, in second. Monotonically non-decreasing within a test; behaviour during pauses is instrument-defined and values must be preserved as reported. |
| Voltage / V | `voltage_volt` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt](https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt) | Instantaneous voltage measured across the terminals of the test object, in volt. |
<!-- END GENERATED: bdf-terms-required -->


3. **Recommended quantities**

<!-- BEGIN GENERATED: bdf-terms-recommended -->
<!-- Generated from BDF ontology 1.2.0 by scripts/generate_docs.py - do not edit by hand. -->
| Preferred Label | Machine-readable name | IRI | Description |
|---|---|---|---|
| Ambient Temperature / degC | `ambient_temperature_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius) | Ambient temperature recorded during testing, in degree Celsius. |
| Cycle Count / 1 | `cycle_count` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count) | Cycle index, non-negative integer, monotonically non-decreasing within a test. Starting value is instrument-defined (any non-negative integer); converters must not renumber cycles. |
| Step Count / 1 | `step_count` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count) | Monotonically increasing sequential counter incremented by one each time a new step begins, for the duration of the test. Unlike Step ID, this counter never resets and never repeats, making it a unique identifier for each step execution across all cycles. |
| Unix Time / s | `unix_time_second` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second) | Timestamp expressed as Unix time, in second: seconds elapsed since 1970-01-01T00:00:00 UTC (the Unix epoch), excluding leap seconds. The value identifies an absolute instant and is independent of local time zone: the same physical moment has the same value everywhere, and local wall-clock times must be converted to UTC before encoding. |
<!-- END GENERATED: bdf-terms-recommended -->


4. **Optional quantities**

<!-- BEGIN GENERATED: bdf-terms-optional -->
<!-- Generated from BDF ontology 1.2.0 by scripts/generate_docs.py - do not edit by hand. -->
<details>
<summary><b>47 optional quantities</b> &mdash; click to expand</summary>

| Preferred Label | Machine-readable name | IRI | Description |
|---|---|---|---|
| AC Internal Resistance / ohm | `ac_internal_resistance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#ac_internal_resistance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#ac_internal_resistance_ohm) | AC internal resistance: impedance magnitude at a fixed excitation frequency, conventionally 1 kHz. Equivalent to instrument ACR outputs. |
| Absolute Impedance / ohm | `absolute_impedance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#absolute_impedance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#absolute_impedance_ohm) | The magnitude of the complex impedance, in ohm. |
| Ambient Pressure / Pa | `ambient_pressure_pa` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa) | Ambient air pressure recorded during testing, in pascal. |
| Applied Pressure / Pa | `applied_pressure_pa` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa) | External pressure actively applied to the test object and controlled by an external agent (e.g. a fixture, clamp, or press), in pascal. Distinguished from surface_pressure_pa, which is the pressure measured at the surface of the test object regardless of its source. |
| Charging Capacity / Ah | `charging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah) | Cumulative electric charge transferred into the test object during charging since test start, in ampere hour. Never resets between steps or cycles. |
| Charging Energy / Wh | `charging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh) | Cumulative energy transferred into the test object during charging since test start, in watt hour. Never resets between steps or cycles. |
| Cumulative Capacity / Ah | `cumulative_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah) | Total Ah throughput since test start: charging_capacity_ah + discharging_capacity_ah. Always monotonically non-decreasing. |
| Cumulative Energy / Wh | `cumulative_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh) | Total Wh throughput since test start: charging_energy_wh + discharging_energy_wh. Always monotonically non-decreasing. |
| Cycle Charging Capacity / Ah | `cycle_charging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_charging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_charging_capacity_ah) | Electric charge transferred into the test object during the charge portions of the current cycle, in ampere hour. Non-negative; resets to zero when cycle_count increments. |
| Cycle Charging Energy / Wh | `cycle_charging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_charging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_charging_energy_wh) | Energy transferred into the test object during the charge portions of the current cycle, in watt hour. Non-negative; resets to zero when cycle_count increments. |
| Cycle Cumulative Capacity / Ah | `cycle_cumulative_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_cumulative_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_cumulative_capacity_ah) | Running accumulation of charge throughput within the current cycle, in ampere hour. Monotonically non-decreasing within the cycle; resets to zero when cycle_count increments. |
| Cycle Cumulative Energy / Wh | `cycle_cumulative_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_cumulative_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_cumulative_energy_wh) | Running accumulation of energy throughput within the current cycle, in watt hour. Monotonically non-decreasing within the cycle; resets to zero when cycle_count increments. |
| Cycle Discharging Capacity / Ah | `cycle_discharging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_discharging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_discharging_capacity_ah) | Electric charge transferred out of the test object during the discharge portions of the current cycle, in ampere hour. Non-negative; resets to zero when cycle_count increments. |
| Cycle Discharging Energy / Wh | `cycle_discharging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_discharging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_discharging_energy_wh) | Energy transferred out of the test object during the discharge portions of the current cycle, in watt hour. Non-negative; resets to zero when cycle_count increments. |
| Cycle Net Capacity / Ah | `cycle_net_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_net_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_net_capacity_ah) | Running net capacity within the current cycle: cycle_charging_capacity_ah - cycle_discharging_capacity_ah. Can be negative; resets to zero when cycle_count increments. |
| Cycle Net Energy / Wh | `cycle_net_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_net_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_net_energy_wh) | Running net energy within the current cycle: cycle_charging_energy_wh - cycle_discharging_energy_wh. Can be negative; resets to zero when cycle_count increments. |
| DC Internal Resistance / ohm | `dc_internal_resistance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#dc_internal_resistance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#dc_internal_resistance_ohm) | DC internal resistance from the voltage response to a current step. Method parameters are instrument-specific; values from different methods are not directly comparable. |
| Discharging Capacity / Ah | `discharging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah) | Cumulative electric charge transferred out of the test object during discharging since test start, in ampere hour. Never resets between steps or cycles. |
| Discharging Energy / Wh | `discharging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh) | Cumulative energy transferred out of the test object during discharging since test start, in watt hour. Never resets between steps or cycles. |
| Frequency / Hz | `frequency_hertz` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#frequency_hertz](https://w3id.org/battery-data-alliance/ontology/battery-data-format#frequency_hertz) | The frequency of the applied periodic excitation or measured response, in hertz. |
| Imaginary Impedance / ohm | `imaginary_impedance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#imaginary_impedance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#imaginary_impedance_ohm) | The imaginary (reactive) component of the complex impedance, in ohm. Sign as reported: negative for predominantly capacitive behaviour. |
| Internal Resistance / ohm | `internal_resistance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm) | Internal resistance of the test object, in ohm, as reported by the instrument; the determination method is unspecified. Where the determination method is known, use the more specific dc_internal_resistance_ohm or ac_internal_resistance_ohm. |
| Net Capacity / Ah | `net_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah) | Running net capacity from test start: charging_capacity_ah - discharging_capacity_ah. Can be negative. Equivalent to BioLogic Q-Q0. |
| Net Energy / Wh | `net_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh) | Running net energy from test start: charging_energy_wh - discharging_energy_wh. Can be negative. |
| Phase / deg | `phase_degree` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#phase_degree](https://w3id.org/battery-data-alliance/ontology/battery-data-format#phase_degree) | The phase angle of the complex impedance, representing the phase shift between voltage and current, in degree. |
| Power / W | `power_watt` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt](https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt) | Instantaneous power calculated as the product of voltage and current, in watt. |
| Real Impedance / ohm | `real_impedance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#real_impedance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#real_impedance_ohm) | The real (resistive) component of the complex impedance, in ohm. |
| Record Index / 1 | `record_index` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#record_index](https://w3id.org/battery-data-alliance/ontology/battery-data-format#record_index) | An ordinal, dimensionless integer used to order data records within a time-series dataset, incremented by one for each recorded record and carrying no physical or quantitative meaning. |
| Step Charging Capacity / Ah | `step_charging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_capacity_ah) | Electric charge transferred into the test object during the charge portion of the current test step, in ampere hour. Non-negative; resets to zero at each step transition. |
| Step Charging Energy / Wh | `step_charging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_energy_wh) | Energy transferred into the test object during the charge portion of the current test step, in watt hour. Non-negative; resets to zero at each step transition. |
| Step Cumulative Capacity / Ah | `step_cumulative_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_capacity_ah) | Running accumulation of charge throughput within the current test step, in ampere hour. Monotonically non-decreasing within the step; resets to zero at each step transition. |
| Step Cumulative Energy / Wh | `step_cumulative_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_energy_wh) | Running accumulation of energy throughput within the current test step, in watt hour. Monotonically non-decreasing within the step; resets to zero at each step transition. |
| Step Discharging Capacity / Ah | `step_discharging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_capacity_ah) | Electric charge transferred out of the test object during the discharge portion of the current test step, in ampere hour. Non-negative; resets to zero at each step transition. |
| Step Discharging Energy / Wh | `step_discharging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_energy_wh) | Energy transferred out of the test object during the discharge portion of the current test step, in watt hour. Non-negative; resets to zero at each step transition. |
| Step ID | `step_id` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_id](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_id) | The step identifier as defined in the test program or schedule file. Values are instrument-defined and are not required to be contiguous or monotonically increasing; the same step ID may recur in successive cycles. Known equivalents: Arbin Step_Index, Neware Step_ID, Digatron Step, BioLogic Ns. |
| Step Index / 1 | `step_index` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index) | 1-based positional counter for data points within a step. Resets to 1 at the start of each new step and increments by 1 for each subsequent recorded data point. Always derivable from the data; not typically exported directly by cycler software. This is the within-step data-point counter, not the program step identifier: an instrument column named 'Step Index' (e.g. Arbin Step_Index) maps to step_id, not to this property. |
| Step Net Capacity / Ah | `step_net_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_capacity_ah) | Running net capacity within the current test step: step_charging_capacity_ah - step_discharging_capacity_ah. Can be negative; resets to zero at each step transition. |
| Step Net Energy / Wh | `step_net_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_energy_wh) | Running net energy within the current test step: step_charging_energy_wh - step_discharging_energy_wh. Can be negative; resets to zero at each step transition. |
| Step Time / s | `step_time_second` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_time_second) | Elapsed time since the beginning of the active test step, in second; resets to zero at each step transition. |
| Step Type | `step_type` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_type](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_type) | A string label describing the operational mode of the current test step, as reported by the cycler software. Common values include CC_CHG, CC_DCH, CV_CHG, CCCV_CHG, REST, OCV, EIS. |
| Surface Pressure / Pa | `surface_pressure_pa` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_pressure_pa) | Pressure measured at the surface of the test object, in pascal. A measured quantity that may be nonzero even when no external pressure is actively applied, e.g. from cell swelling against a fixture. Distinguished from applied_pressure_pa, which is actively applied and controlled by an external agent. |
| Surface Temperature / degC | `surface_temperature_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_temperature_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_temperature_celsius) | Temperature measured at the surface of the test object, in degree Celsius, regardless of sensor channel. |
| Temperature T1 / degC | `temperature_t1_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius) | Temperature recorded by auxiliary temperature channel 1, in degree Celsius. Sensor placement is setup-defined; record it in accompanying metadata. |
| Temperature T2 / degC | `temperature_t2_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius) | Temperature recorded by auxiliary temperature channel 2, in degree Celsius. Sensor placement is setup-defined; record it in accompanying metadata. |
| Temperature T3 / degC | `temperature_t3_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius) | Temperature recorded by auxiliary temperature channel 3, in degree Celsius. Sensor placement is setup-defined; record it in accompanying metadata. |
| Temperature T4 / degC | `temperature_t4_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius) | Temperature recorded by auxiliary temperature channel 4, in degree Celsius. Sensor placement is setup-defined; record it in accompanying metadata. |
| Temperature T5 / degC | `temperature_t5_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius) | Temperature recorded by auxiliary temperature channel 5, in degree Celsius. Sensor placement is setup-defined; record it in accompanying metadata. |

</details>
<!-- END GENERATED: bdf-terms-optional -->


5. **Data structure**:
   - The first row contains a header row with the preferred label of the quantity in the corresponding column.
   - The units of the quantities are fixed.
   - All rows must match the initial header in column count, ensuring consistent formatting.

6. **File naming conventions**:
   - Recommended format:  
     ```
     InstitutionCode__CellName__YYYYMMDD_XXX.csv
     ```
     Example:  
     ```
     UCam__A0001__20241031_001.csv
     ```
   - Additional metadata (e.g., cell model, serial number, unique identifier) should be stored within a parallel metadata file.

7. **File extension**:
   - Files using text-based serialization can be saved with the `.bdf` extension.  
   - If another extension is necessary, it can be supplemented with a `.bdf` prefix (e.g. `example.bdf.parquet`)  
   - Stream compressors like `gzip` can be used to save space, resulting in `.bdf.gz` files.
   - It is assumed that `.bdf` files adhere to the BDF conventions, and future validator functions may be created to enforce this.

## Summary

The **Battery Data Format (.bdf)** is a step toward unifying and accelerating battery research and development. By adopting this open-source standard, we can foster collaboration, enhance model interoperability, and unlock the full potential of data-driven battery innovation.

## Install the Python Package

```bash
pip install batterydf
```

Optional extras add support for specific vendor formats or plotting backends.
Combine as needed, e.g. `batterydf[nda,plot]`, or use `batterydf[all]` to get everything:
```bash
pip install batterydf[nda]      # Neware .nda/.ndax (using fastnda)
pip install batterydf[mpr]      # BioLogic .mpr (using yadg)
pip install batterydf[excel]    # Excel (using calamine)
pip install batterydf[mat]      # MATLAB .mat files (using scipy)
pip install batterydf[plot]     # For plotting with matplotlib/plotly
pip install batterydf[hvplot]   # For plotting with bokeh/holoviews
pip install batterydf[yaml]     # For YAML plugin definitions
pip install batterydf[all]      # Everything above
```

PyPI distribution name is ``batterydf``; Python import and CLI remain ``bdf``.

### Quickstart

```python
import bdf

# Read raw or BDF; plugin auto-detects
df = bdf.read("path/to/file.bdf.csv")

# Read Neware .nda/.ndax (requires fastnda: pip install batterydf[nda])
df = bdf.read("path/to/file.nda")

# Force the NDA plugin explicitly
df = bdf.read("path/to/file.nda", plugin="neware_nda")

# Validate
report = bdf.validate(df, report=True, raise_on_error=False)

# Repair time/outliers
df_clean, rep = bdf.clean(df, time_fix="segment", outlier="none")

# Plot
bdf.plot(df_clean, xdata="Test Time / s", ydata=["Voltage / V"], save="plot.png")

# Interactive exploration (plotly requires batterydf[plot]; bokeh requires batterydf[hvplot])
bdf.explore(df_clean, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A", backend="bokeh")
bdf.explore(df_clean, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A", backend="plotly")

# Ingest a folder of raw files into BDF artifacts
summary = bdf.ingest("data/raw", out_dir="data/bdf", format="parquet")
```

CLI examples:

```bash
bdf validate data/sample.bdf.csv
bdf clean data/sample.bdf.csv --out cleaned.bdf.csv --assume-bdf
bdf convert raw/vendor.csv --to output.bdf.csv
bdf plot data/sample.bdf.csv --assume-bdf --save plot.png
bdf meta-jsonld data/sample.bdf.csv --title "My dataset" --description "..." --creator "Name|ORCID|Affiliation"
bdf templates contribution battery excel --root my-contribution
bdf ingest my-contribution --raw-dir timeseries/raw --data-dir timeseries
```

### Documentation

Full docs (API, CLI, examples) are built with Sphinx/pydata theme. After build, browse `docs/_build/html/index.html`. On GitHub Pages, use the project site.

## FAQ

### Which label should I use for my column headings?

You should use the Preferred Label for your column headings. This is the label that is designed to adhere to recommendations for human-readable titles and corresponds to the `csvw:titles` property in the table schema.

### What is the difference between the preferred label and the machine-readable name?

The preferred label is designed to be readable for humans and adhere to IUPAC / SI guidelines for quantity notation. But the preferred label contains some characters (e.g. spaces and slashes) that can create difficulty for some machines. The machine-readable name is designed to be an alias for referring to the quantity in software. It is linked to the preferred label in both the BDF application ontology and the CSVW table schema.

### Why do we use a slash between the quantity and the unit?

This is the notation that is recommended by authoritative bodies like IUPAC and SI. The slash comes from the fact that quantities are the product of a value and a unit, and they obey the rules of algebra. For example, if we say that `Voltage = 4.2 V` and divide both sides of the equation by the unit, we get `Voltage / V = 4.2`

### How can I check if my file is a valid instance of BDF?

Use the built-in validator. From Python:

```python
import bdf

df = bdf.read("path/to/file.bdf.csv")
report = bdf.validate(df, report=True, raise_on_error=False)
```

Or from the command line:

```bash
bdf validate path/to/file.bdf.csv
```

The validator checks column headers and units against the BDF ontology, structural rules such as time monotonicity, and the ontology-defined relationships between derived columns (e.g. that cumulative capacity equals charging plus discharging capacity).
