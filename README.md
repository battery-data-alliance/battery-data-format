# Battery Data Format for Time-Series Data (.bdf)

## Why introduce an industry standard format for battery data?

It is well known that organizing, cleaning, and preparing battery data for analytics takes significant time and effort, creating a high barrier to leveraging advances in battery modeling for battery development cycles.

A 2024 Forrester study surveyed 165 decision-makers in the automotive industry responsible for EV battery testing, validation, and development in the US and Europe. Among the respondents, 57% cited deciphering complex relationships in vast, multiparameter datasets as a significant barrier to battery validation, and 61% estimated months to years of time savings from AI-powered cell characterization testing that leverages standardized data sets.

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

| Preferred Label       | Machine-readable name   | Description                                                                 | IRI                                                                 |
|-----------------------|--------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------|
| Test Time / ms        | `test_time_millisecond`  | Elapsed time since the start of the test, recorded in millisecond.         | https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_millisecond |
| Current / A           | `current_ampere`         | Instantaneous current applied to or from the test object, in ampere.       | https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere |
| Voltage / V           | `voltage_volt`           | Instantaneous voltage measured across the test object, in volt.            | https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt |

### Recommended quantities  

| Preferred Label              | Machine-readable name         | Description                                                                                         | IRI                                                                 |
|------------------------------|-------------------------------|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| Unix Time / ms               | `unix_time_millisecond`       | Timestamp of the measurement in Unix time (millisecond since Jan 1, 1970 UTC). Used to align data. | https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_millisecond |
| Cycle Count / 1              | `cycle_count`                 | Sequential index of the current cycle in the test program, increasing monotonically throughout the dataset. | https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count |
| Step Count / 1               | `step_count`                  | Sequential index of the current step in the test program, increasing monotonically throughout the dataset. | https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count |
| Temperature / °C             | `temperature_celsius`         | Measured temperature of the test object (e.g., surface or internal), in degree Celsius.             | https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_celsius |

### Optional quantities 

| Preferred Label                    | Machine-readable name             | Description                                                                 | IRI                                                                 |
|------------------------------------|-----------------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------|
| Step Index / 1                     | `step_index`                      | Index identifying the type of step being executed, as defined in the test program. May repeat across the dataset. | https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index |
| Charging Capacity / Ah             | `charging_capacity_ampere_hour`     | Accumulated capacity during a charging phase, in ampere hour.              | https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ampere_hour |
| Discharging Capacity / Ah          | `discharging_capacity_ampere_hour`  | Accumulated capacity during a discharging phase, in ampere hour.           | https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ampere_hour |
| Charging Energy / Wh               | `charging_energy_watt_hour`         | Accumulated energy during a charging phase, in watt hour.                  | https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_watt_hour |
| Discharging Energy / Wh            | `discharging_energy_watt_hour`      | Accumulated energy during a discharging phase, in watt hour.               | https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_watt_hour |
| Internal Resistance / Ohm          | `internal_resistance_ohm`         | Internal resistance of the test object, measured in ohm.                   | https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm |
| Ambient Temperature / °C           | `ambient_temperature_celsius`     | Temperature of the surrounding environment, in degree Celsius.             | https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius |
| Ambient Pressure / Pa              | `ambient_pressure_pascal`         | Atmospheric pressure in the test environment, in pascal.                   | https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pascal |
| Applied Pressure / Pa              | `applied_pressure_pascal`         | Mechanical pressure applied directly to the test object, in pascal.        | https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pascal |


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
