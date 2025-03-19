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
  _Note: BattInfo was previously developed to describe battery metadata naming conventions and to outline definitions of metrics that can be computed from time-series data._
- An immediate next step will be launching a parallel format for storing metadata for the BDF.
- Future development will focus on formats for other types of lab data such as impedance data.

## Defining the BDF for Cycler Time-Series Data

1. **Each file contains time-series data for one and only one cell.**  
   - Multiple files can be provided for the same cell.

2. **Required measurement types**:
   - `"test_time_millisecond"` (int): Number of milliseconds since the start of the test.
   - `"current_ampere"` (float): Instantaneous current, recorded in amperes.
   - `"voltage_volt"` (float): Instantaneous voltage, recorded in volts.
   - `"cycle_dimensionless"` (int): Used to track evolution of performance metrics over the course of aging.

3. **Recommended additional measurement**:
   - `"date_time_millisecond"`: Encodes actual date and time when the measurement was made using Unix time in milliseconds.
   - Necessary to support data stitching across multiple files for the same dataset identifier and connect cycler data to other physical events in lab environments.

4. **Optional measurement types**:
   - `"ambient_temperature_celsius"` (float)
   - `"ambient_pressure_pascal"` (float)
   - `"surface_temperature_celsius"` (float)
   - `"surface_pressure_pascal"` (float)
   - `"dcir_ohm"` (float)
   - `"step_dimensionless"` (int)
   - _Note: The string after the final underscore denotes the unit associated with the variable name. The `pint` library is recommended for unit selection._

5. **Data structure**:
   - The first row contains a header row indicating the measurement name and units.
   - The units of required measurement types are fixed.
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
   - Files can be saved with `.bdf` or `.csv`.
   - It is assumed that `.bdf` files adhere to the BDF conventions, and future validator functions may be created to enforce this.
   - Stream compressors like `gzip` can be used to save space, resulting in `.bdf.gz` files.

## Conclusion

The **Battery Data Format (.bdf)** is a step toward unifying and accelerating battery research and development. By adopting this open-source standard, we can foster collaboration, enhance model interoperability, and unlock the full potential of data-driven battery innovation.
