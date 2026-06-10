# Battery Data Format (.bdf)

## Why introduce an industry standard format for battery data?

It is well known that organizing, cleaning, and preparing battery data for analytics takes significant time and effort, creating a high barrier to leveraging advances in battery modeling for battery development cycles.

[A 2024 Forrester study](https://25090789.fs1.hubspotusercontent-eu1.net/hubfs/25090789/FORRESTER%20DRAFT_PLACEHOLDER.pdf) surveyed 165 decision-makers in the automotive industry responsible for EV battery testing, validation, and development in the US and Europe. Among the respondents, 57% cited deciphering complex relationships in vast, multiparameter datasets as a significant barrier to battery validation, and 61% estimated months to years of time savings from AI-powered cell characterization testing that leverages standardized data sets.

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

2. **Required quantities**  

<!-- BEGIN GENERATED: bdf-terms-required -->
<!-- Generated from BDF ontology 1.1.0 by scripts/generate_docs.py - do not edit by hand. -->
| Preferred Label | Machine-readable name | IRI | Description |
|---|---|---|---|
| Current / A | `current_ampere` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere](https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere) | Instantaneous current recorded in ampere. |
| Test Time / s | `test_time_second` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second) | Test time recorded in second. |
| Voltage / V | `voltage_volt` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt](https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt) | Instantaneous voltage recorded in volt. |
<!-- END GENERATED: bdf-terms-required -->


3. **Recommended quantities**

<!-- BEGIN GENERATED: bdf-terms-recommended -->
<!-- Generated from BDF ontology 1.1.0 by scripts/generate_docs.py - do not edit by hand. -->
| Preferred Label | Machine-readable name | IRI | Description |
|---|---|---|---|
| Ambient Temperature / degC | `ambient_temperature_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius) | Ambient temperature recorded during testing, in degree Celsius. |
| Cycle Count / 1 | `cycle_count` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count) | Cycle index, non-negative integer, monotonically non-decreasing within a test. Starting value is instrument-defined (any non-negative integer); converters must not renumber cycles. |
| Step Count / 1 | `step_count` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count) | Monotonically increasing sequential counter incremented by one each time a new step begins, for the duration of the test. Unlike Step ID, this counter never resets and never repeats, making it a unique identifier for each step execution across all cycles. |
| Unix Time / s | `unix_time_second` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second) | Unix time recorded in second. |
<!-- END GENERATED: bdf-terms-recommended -->


4. **Optional quantities**

<!-- BEGIN GENERATED: bdf-terms-optional -->
<!-- Generated from BDF ontology 1.1.0 by scripts/generate_docs.py - do not edit by hand. -->
| Preferred Label | Machine-readable name | IRI | Description |
|---|---|---|---|
| Absolute Impedance / ohm | `absolute_impedance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#absolute_impedance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#absolute_impedance_ohm) | the magnitude of the complex impedance, expressed in ohms. |
| Ambient Pressure / Pa | `ambient_pressure_pa` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa) | Ambient air pressure recorded during testing, in pascal. |
| Applied Pressure / Pa | `applied_pressure_pa` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa) | External pressure applied to the test object, in pascal. |
| Charging Capacity / Ah | `charging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah) | Cumulative electric charge transferred into the test object during charging since test start, in ampere hour. Never resets between steps or cycles. |
| Charging Energy / Wh | `charging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh) | Cumulative energy transferred into the test object during charging since test start, in watt hour. Never resets between steps or cycles. |
| Cumulative Capacity / Ah | `cumulative_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah) | Total Ah throughput since test start: charging_capacity_ah + discharging_capacity_ah. Always monotonically non-decreasing. |
| Cumulative Energy / Wh | `cumulative_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh) | Total Wh throughput since test start: charging_energy_wh + discharging_energy_wh. Always monotonically non-decreasing. |
| Discharging Capacity / Ah | `discharging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah) | Cumulative electric charge transferred out of the test object during discharging since test start, in ampere hour. Never resets between steps or cycles. |
| Discharging Energy / Wh | `discharging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh) | Cumulative energy transferred out of the test object during discharging since test start, in watt hour. Never resets between steps or cycles. |
| Frequency / Hz | `frequency_hertz` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#frequency_hertz](https://w3id.org/battery-data-alliance/ontology/battery-data-format#frequency_hertz) | the frequency of an applied periodic excitation or measured response, expressed in hertz. |
| Imaginary Impedance / ohm | `imaginary_impedance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#imaginary_impedance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#imaginary_impedance_ohm) | the imaginary component of the complex impedance, expressed in ohms. |
| Internal Resistance / ohm | `internal_resistance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm) | Direct current internal resistance recorded in ohm. |
| Net Capacity / Ah | `net_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah) | Running net capacity from test start: charging_capacity_ah - discharging_capacity_ah. Can be negative. Equivalent to BioLogic Q-Q0. |
| Net Energy / Wh | `net_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh) | Running net energy from test start: charging_energy_wh - discharging_energy_wh. Can be negative. |
| Phase / deg | `phase_degree` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#phase_degree](https://w3id.org/battery-data-alliance/ontology/battery-data-format#phase_degree) | the phase angle of the complex impedance in electrochemical impedance spectroscopy, defined as the argument of the impedance and representing the phase relationship between voltage and current, expressed in degrees. |
| Power / W | `power_watt` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt](https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt) | Instantaneous power calculated as the product of voltage and current, in watt. |
| Real Impedance / ohm | `real_impedance_ohm` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#real_impedance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#real_impedance_ohm) | the real component of the complex impedance, expressed in ohms. |
| Record Index / 1 | `record_index` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#record_index](https://w3id.org/battery-data-alliance/ontology/battery-data-format#record_index) | an ordinal, dimensionless integer used to order data records in a time-series dataset, incremented by one for each recorded record and carrying no physical or quantitative meaning |
| Step Charging Capacity / Ah | `step_charging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_capacity_ah) | Electric charge transferred into the test object during the charge portion of the current test step, in ampere hour. Non-negative; resets to zero at each step transition. |
| Step Charging Energy / Wh | `step_charging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_charging_energy_wh) | Energy transferred into the test object during the charge portion of the current test step, in watt hour. Non-negative; resets to zero at each step transition. |
| Step Cumulative Capacity / Ah | `step_cumulative_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_capacity_ah) | Running accumulation of charge throughput within the current test step, in ampere hour. Monotonically non-decreasing within the step; resets to zero at each step transition. |
| Step Cumulative Energy / Wh | `step_cumulative_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_cumulative_energy_wh) | Running accumulation of energy throughput within the current test step, in watt hour. Monotonically non-decreasing within the step; resets to zero at each step transition. |
| Step Discharging Capacity / Ah | `step_discharging_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_capacity_ah) | Electric charge transferred out of the test object during the discharge portion of the current test step, in ampere hour. Non-negative; resets to zero at each step transition. |
| Step Discharging Energy / Wh | `step_discharging_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_discharging_energy_wh) | Energy transferred out of the test object during the discharge portion of the current test step, in watt hour. Non-negative; resets to zero at each step transition. |
| Step Index / 1 | `step_index` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index) | 1-based positional counter for data points within a step. Resets to 1 at the start of each new step and increments by 1 for each subsequent recorded data point. Always derivable from the data; not typically exported directly by cycler software. This is the within-step data-point counter, not the program step identifier: an instrument column named 'Step Index' (e.g. Arbin Step_Index) maps to step_id, not to this property. |
| Step Net Capacity / Ah | `step_net_capacity_ah` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_capacity_ah) | Running net capacity within the current test step: step_charging_capacity_ah - step_discharging_capacity_ah. Can be negative; resets to zero at each step transition. |
| Step Net Energy / Wh | `step_net_energy_wh` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_net_energy_wh) | Running net energy within the current test step: step_charging_energy_wh - step_discharging_energy_wh. Can be negative; resets to zero at each step transition. |
| Step Time / s | `step_time_second` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_time_second) | the elapsed time since the beginning of the active test step, measured in seconds and reset at each step transition |
| Surface Pressure / Pa | `surface_pressure_pa` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_pressure_pa) | Surface pressure recorded in pascal. |
| Surface Temperature / degC | `surface_temperature_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_temperature_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#surface_temperature_celsius) | Surface temperature recorded in degree Celsius. |
| Surface Temperature T1 / degC | `temperature_t1_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius) | For tests with multiple temperature measurements, the measured temperature of the test object (e.g., surface or internal), in degrees Celsius. |
| Surface Temperature T2 / degC | `temperature_t2_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius) | For tests with multiple temperature measurements, the measured temperature of the test object (e.g., surface or internal), in degrees Celsius. |
| Surface Temperature T3 / degC | `temperature_t3_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius) | For tests with multiple temperature measurements, the measured temperature of the test object (e.g., surface or internal), in degrees Celsius. |
| Surface Temperature T4 / degC | `temperature_t4_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius) | For tests with multiple temperature measurements, the measured temperature of the test object (e.g., surface or internal), in degrees Celsius. |
| Surface Temperature T5 / degC | `temperature_t5_celsius` | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius) | For tests with multiple temperature measurements, the measured temperature of the test object (e.g., surface or internal), in degrees Celsius. |
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

## Install the Pyton Package

```bash
pip install batterydf
# Neware .nda/.ndax support is included in base
# Interactive plotting (hvplot/bokeh)
pip install batterydf[hvplot]
# Polars + fast NDA backend
pip install batterydf[polars]
# Force numpy 2.x (combine as needed, e.g. batterydf[polars,numpy2])
pip install batterydf[numpy2]
# for docs/dev: pip install -e .[dev,docs]
```

PyPI distribution name is ``batterydf``; Python import and CLI remain ``bdf``.

Optional fast NDA backend (Python >=3.10, numpy >=2.2 required):
```bash
pip install fastnda
```
Note: fastnda requires numpy >=2.2. If you need fastnda, install with the numpy 2.x extra,
for example `batterydf[polars,numpy2]` or `batterydf[numpy2]`.

### Quickstart

```python
import bdf

# Read raw or BDF; plugin auto-detects
df = bdf.read("path/to/file.bdf.csv")

# Read Neware .nda/.ndax (supported by default)
df = bdf.read("path/to/file.nda")

# Force the fast NDA backend if installed
df = bdf.read("path/to/file.nda", plugin="neware-nda-fast")

# Interactive exploration (plotly included in base; bokeh requires batterydf[hvplot])
bdf.explore(df, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A", backend="bokeh")
bdf.explore(df, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A", backend="plotly")

# Validate
report = bdf.validate(df, report=True, raise_on_error=False)

# Repair time/outliers
df_clean, rep = bdf.clean(df, time_fix="segment", outlier="none")

# Plot
bdf.plot(df_clean, xdata="Test Time / s", ydata=["Voltage / V"], save="plot.png")

# Interactive exploration (plotly included in base; bokeh requires batterydf[hvplot])
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

The preferred label is designed to be readable for humans and adhere to IUPAC / SI guidelines for quantity notation. But the preferred label contains some characters (e.g. spaces and slashes) that can create difficulty for some machines. The machine-readable name is designed to be an alias for referring to the quantity in software. It is linked to the preferred label in both the BDF applicaiton ontology and the CSVW table schema. 

### Why do we use a slash between the quantity and the unit?

This is the notation that is recommended by authoritative bodies like IUPAC and SI. The slash comes from the fact that quantities are the product of a value and a unit, and they obey the rules of algebra. For example, if we say that `Voltage = 4.2 V` and divide both sides of the equation by the unit, we get `Voltage / V = 4.2`

### How can I check if my file is a valid instance of BDF?
