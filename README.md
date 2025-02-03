# Battery Data Format for Time-Series Data (.bdf)

## Why Introduce an Industry Standard Format for Battery Data?

Organizing, cleaning, and preparing battery data for analytics takes significant time and effort, creating a high barrier to leveraging advances in battery modeling for battery development cycles.

A 2024 Forrester study surveyed 165 decision-makers in the automotive industry responsible for EV battery testing, validation, and development in the US and Europe. Among the respondents:

- **57%** cited deciphering complex relationships in vast, multiparameter datasets as a significant barrier to battery validation.
- **61%** estimated **months to years** of time savings from AI-powered cell characterization testing that leverages standardized datasets.

## Goal of Launching the BDF

The **Battery Data Format (BDF)** provides a standard structure for data generated in battery labs, offered to the community by the **Battery Data Alliance**, a Linux Foundation Energy project. Adoption of the BDF aims to empower the battery science community to leverage advances in open-source battery models.

Developed with input from leading scientists and engineers, the BDF addresses two main challenges:

- **Data Consistency:** A common format eliminates inconsistencies in data structure across different software updates.
- **Model Compatibility:** A unified format allows battery model developers to easily adapt their models to accept BDF data, enabling seamless experimentation with multiple models without custom coding.

## Initial Scope of the BDF

- The initial scope facilitates the **use and comparison of cycler time-series data**.
- **BattInfo** was previously developed to define battery metadata naming conventions and outline definitions of metrics computed from time-series data.
- A next step involves launching a **parallel format for storing metadata** for the BDF.
- Future development will focus on **formats for other types of lab data**, such as **impedance data**.

## Defining the BDF for Cycler Time-Series Data

### 1. Each file contains time-series data for one and only one cell.
   - Multiple files can be provided for the same cell.

### 2. Required Measurement Types
   - **Time (absolute UNIX milliseconds)** – Ensures seamless data stitching across multiple files.
   - **"current_amps" (float)** – Instantaneous current, recorded in amperes.
   - **"voltage_volts" (float)** – Instantaneous voltage, recorded in volts.
   - **"cycle_number" (int)** – Used to segregate data into quasi-periodic subsets to track performance metrics over aging.

### 3. Optional Measurement Types
   These should follow standardized naming and unit conventions:
   - **"temperature_celsius" (float)**
   - **"pressure_atm" (float)**
   - **"dcir_ohm" (float)**
   - **"step_number" (int)**
   - Other types should use the format: `measurement_units`, e.g., `capacity_mAh`.

### 4. Data Structure
   - The **first row** contains a header indicating the measurement names and units.
   - The units for required measurement types are fixed.
   - **All rows must match the header in column count** to ensure consistency.

### 5. Cell Identifier in Filename
   - **Each filename starts with the cell ID**, followed by an underscore (`_`), then additional test information.
   - **The cell ID does not contain underscores**, ensuring proper sorting and retrieval.

### 6. File Extension
   - Files can be saved as **.bdf** or **.csv**.
   - Files with `.bdf` are assumed to follow BDF conventions, and future validation functions may verify compliance.
   - **Stream compressors like gzip** can be used to save space, resulting in `.bdf.gz` files.

## Conclusion

The **Battery Data Format (.bdf)** is a step toward **unifying and accelerating battery research and development**. By adopting this open-source standard, we can:

- Foster **collaboration**
- Enhance **model interoperability**
- Unlock the **full potential of data-driven battery innovation**
