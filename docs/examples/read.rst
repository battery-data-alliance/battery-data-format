Reading datasets into BDF
=========================

.. container:: cell markdown
   :name: bc76c2e0

   .. rubric:: Reading datasets into BDF
      :name: reading-datasets-into-bdf

   BDF supports a few ways to read source data:

   - a URL for a file hosted on the web
   - a path for a local file
   - an identifier from the BDF data registry

   In this notebook, we will show some examples for how to read data
   from these different sources. We will also demonstrate some helpful
   features including:

   - viewing the raw data in the format supplied from the vendor /
     original source
   - keeping only the required columns or including all columns

.. container:: cell code
   :name: 2ca5d7a6

   .. code:: python

      # Import the package
      import bdf

.. container:: cell code
   :name: bfb4caf5

   .. code:: python

      # Read from a local file path
      filepath = "../data/SINTEF__LiGrR2032__2024-04-30__25degC__Landt.csv"

      df = bdf.read(filepath)
      df.head()

   .. container:: output execute_result

      ::

           Test Time / s Voltage / V Current / A Step Index / 1  \
         0         0.020      2.9215      0.0000              1   
         1        15.020      2.9215      0.0000              1   
         2        30.020      2.9214      0.0000              1   
         3        45.020      2.9214      0.0000              1   
         4        60.020      2.9213      0.0000              1   

           Discharging Capacity / Ah Charging Capacity / Ah Ambient Temperature / degC  
         0                         0                      0                          0  
         1                         0                      0                          0  
         2                         0                      0                          0  
         3                         0                      0                          0  
         4                         0                      0                          0  

.. container:: cell code
   :name: ee17d2f7

   .. code:: python

      # Peek at the raw vendor file without normalization to BDF
      vendor_df = bdf.parse(filepath)
      vendor_df.head()

   .. container:: output execute_result

      ::

           channel_index cycle_index step_index date_time_iso_string test_time_s  \
         0             1           1          1  04/30/2024 14:33:19       0.020   
         1             2           1          1  04/30/2024 14:33:34      15.020   
         2             3           1          1  04/30/2024 14:33:49      30.020   
         3             4           1          1  04/30/2024 14:34:04      45.020   
         4             5           1          1  04/30/2024 14:34:19      60.020   

           step_time_s current_A voltage_V discharge_capacity_Ah charge_capacity_Ah  \
         0       0.020    0.0000    2.9215                     0                  0   
         1      15.020    0.0000    2.9215                     0                  0   
         2      30.020    0.0000    2.9214                     0                  0   
         3      45.020    0.0000    2.9214                     0                  0   
         4      60.020    0.0000    2.9213                     0                  0   

           discharge_energy_Wh charge_energy_Wh Pressure_Psi temperature_1_C  \
         0                   0                0            0               0   
         1                   0                0            0               0   
         2                   0                0            0               0   
         3                   0                0            0               0   
         4                   0                0            0               0   

           temperature_2_C temperature_3_C step_name  
         0               0               0      rest  
         1               0               0      rest  
         2               0               0      rest  
         3               0               0      rest  
         4               0               0      rest  

.. container:: cell code
   :name: 45c48ce8

   .. code:: python

      # Keep only the BDF required columns
      df_req = bdf.read(filepath, include_optional=False)
      print(df_req.columns.tolist())

      # Include all the columns from the raw data that have a BDF equivalent
      df_all = bdf.read(filepath, include_optional=True)
      print(df_all.columns.tolist()[:12])

   .. container:: output stream stdout

      ::

         ['Test Time / s', 'Voltage / V', 'Current / A']
         ['Test Time / s', 'Voltage / V', 'Current / A', 'Step Index / 1', 'Discharging Capacity / Ah', 'Charging Capacity / Ah', 'Ambient Temperature / degC']

.. container:: cell code
   :name: 1a28fe30

   .. code:: python

      # Read data on the Web from a URL
      df = bdf.read("https://zenodo.org/records/17289383/files/SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt")
      df.head()

   .. container:: output execute_result

      ::

            Test Time / s  Voltage / V  Current / A  Ambient Temperature / degC  \
         0            0.0      1.71423          0.0                   23.509834   
         1           10.0     1.714152          0.0                   23.162155   
         2      20.000001     1.714152          0.0                   23.383406   
         3      30.000001      1.71423          0.0                   23.328093   
         4      40.000002     1.714191          0.0                   23.375504   

            Cycle Count / 1  
         0              0.0  
         1              0.0  
         2              0.0  
         3              0.0  
         4              0.0  

.. container:: cell code
   :name: 8da1d0bf

   .. code:: python


      # Read from the BDF data registry
      # This registry is a local JSON file (datasets.json), separate from the metadata registry.
      import json
      from pathlib import Path

      registry_path = Path("out/datasets.json")
      registry_path.parent.mkdir(parents=True, exist_ok=True)
      registry_path.write_text(json.dumps({
          "schema_version": "0.3",
          "entries": [
              {
                  "id": "sintef-biologic-demo",
                  "url": "https://zenodo.org/records/17289383/files/SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.mpt",
                  "plugin": "biologic-mpt",
              }
          ],
      }, indent=2), encoding="utf-8")

      df = bdf.read("sintef-biologic-demo", registry_path=registry_path)
      df.head()
