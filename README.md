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

| Preferred Label       | Machine-readable name   | IRI                                                                                         | Description                                                                 |
|-----------------------|--------------------------|----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| Test Time / s         | `test_time_second`       | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second)      | Elapsed time since the start of the test, recorded in seconds              |
| Voltage / V           | `voltage_volt`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt](https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt)          | Instantaneous voltage measured across the test object                      |
| Current / A           | `current_ampere`         | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere](https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere)        | Instantaneous current applied to or from the test object                   |


3. **Recommended quantities**

| Preferred Label       | Machine-readable name       | IRI                                                                                               | Description                                                                 |
|------------------------|------------------------------|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| Unix Time / s          | `unix_time_second`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second](https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second)            | Timestamp in Unix time format (seconds since 1970-01-01 UTC)               |
| Cycle Count / 1        | `cycle_count`                | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count)                 | Monotonically increasing index of test cycles                              |
| Step Count / 1         | `step_count`                 | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count)                  | Monotonically increasing index of steps within the program                 |
| Ambient Temperature / degC         | `ambient_temperature_celsius`      | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius)     | Temperature of the surrounding environment during testing                  |


4. **Optional quantities**

| Preferred Label                    | Machine-readable name              | IRI                                                                                                   | Description                                                                 |
|------------------------------------|------------------------------------|--------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| Step Index / 1                     | `step_index`                       | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index)                      | Index indicating the position of the data point within a step              |
| Charging Capacity / Ah             | `charging_capacity_ah`             | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah)            | Capacity accumulated during charging within a given interval       |
| Discharging Capacity / Ah          | `discharging_capacity_ah`          | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah)         | Capacity delivered during discharging within a given interval      |
| Step Capacity / Ah                 | `step_capacity_ah`                 | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_capacity_ah)                | Net capacity change over a given step                                  |
| Net Capacity / Ah                  | `net_capacity_ah`                  | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah)                 | Charging capacity minus discharging capacity within a given interval        |
| Cumulative Capacity / Ah           | `cumulative_capacity_ah`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah)          | Total capacity accumulated over a given interval           |
| Charging Energy / Wh               | `charging_energy_wh`               | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh)              | Energy input during charging, computed as ∫V·I·dt over a charging interval |
| Discharging Energy / Wh            | `discharging_energy_wh`            | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh)           | Energy output during discharging, computed as ∫V·I·dt over a discharging interval |
| Step Energy / Wh                   | `step_energy_wh`                   | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_energy_wh)                  | Net energy change during the current step                                  |
| Net Energy / Wh                    | `net_energy_wh`                    | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh)                   | Charging energy minus discharging energy over a given interval                                   |
| Cumulative Energy / Wh             | `cumulative_energy_wh`             | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh](https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh)            | Total energy accumulated over a given interval             |
| Power / W                          | `power_watt`                       | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt](https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt)                      | Instantaneous power calculated as the product of voltage and current                                    |
| Internal Resistance / Ohm          | `internal_resistance_ohm`          | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm](https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm)         | Internal resistance of the test object                            |
| Ambient Pressure / Pa              | `ambient_pressure_pa`              | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa)             | Ambient air pressure recorded during testing                               |
| Applied Pressure / Pa              | `applied_pressure_pa`              | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa](https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa)             | External pressure applied to the test object                               |
| Surface Temperature T1 / degC      | `temperature_t1_celsius`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius)           | Temperature at external sensor location T1 on the test object              |
| Surface Temperature T2 / degC      | `temperature_t2_celsius`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius)           | Temperature at external sensor location T2 on the test object              |
| Surface Temperature T3 / degC      | `temperature_t3_celsius`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius)           | Temperature at external sensor location T3 on the test object              |
| Surface Temperature T4 / degC      | `temperature_t4_celsius`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius)           | Temperature at external sensor location T4 on the test object              |
| Surface Temperature T5 / degC      | `temperature_t5_celsius`           | [https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius](https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius)           | Temperature at external sensor location T5 on the test object              |


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
pip install bdf[viz,arrow,units]
# Neware .nda/.ndax support
pip install bdf[nda]
# Interactive plotting (hvplot/bokeh)
pip install bdf[hvplot]
# Interactive plotting (plotly backend)
pip install bdf[plotly]
# for docs/dev: pip install -e .[dev,docs]
```

Optional fast NDA backend (Python >=3.10, numpy >=2.2 required):
```bash
pip install fastnda
```
Note: fastnda requires numpy >=2.2, which conflicts with bdf's default numpy<2 pin.
Use a separate environment or install from source with a relaxed numpy constraint if needed.

### Quickstart

```python
import bdf

# Read raw or BDF; plugin auto-detects
df = bdf.read("path/to/file.bdf.csv")

# Read Neware .nda/.ndax (requires NewareNDA)
df = bdf.read("path/to/file.nda")

# Force the fast NDA backend if installed
df = bdf.read("path/to/file.nda", plugin="neware-nda-fast")

# Interactive exploration (bokeh or plotly)
bdf.explore(df, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A", backend="bokeh")
bdf.explore(df, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A", backend="plotly")

# Validate
report = bdf.validate(df, report=True, raise_on_error=False)

# Repair time/outliers
df_clean, rep = bdf.clean_bdf(df, time_fix="segment", outlier="none")

# Plot
bdf.plot(df_clean, xdata="Test Time / s", ydata=["Voltage / V"], save="plot.png")
```

CLI examples:

```bash
bdf validate data/sample.bdf.csv
bdf clean data/sample.bdf.csv --out cleaned.bdf.csv --assume-bdf
bdf convert raw/vendor.csv --to output.bdf.csv
bdf plot data/sample.bdf.csv --assume-bdf --save plot.png
bdf meta-jsonld data/sample.bdf.csv --title "My dataset" --description "..." --creator "Name|ORCID|Affiliation"
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
