Validation
==========

.. container:: cell markdown
   :name: 69216ab5

   .. rubric:: Validation
      :name: validation

   There are multiple ways to check if a file or dataframe are compliant
   with the BDF recommendations using the ``validate()`` function. The
   validate function take as an argument:

   - a filepath
   - a URL
   - a dataframe

   It compares the content of the object with the BDF and returns a
   report.

.. container:: cell code
   :name: 91921f17

   .. code:: python

      # Import the package
      import bdf

.. container:: cell code
   :name: 39044893

   .. code:: python

      # Read from a local file path and convert to bdf
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
   :name: d3ef1d10

   .. code:: python

      # Validate the dataframe and print the report
      report = bdf.validate(df, report=True)
      print(report)

   .. container:: output stream stdout

      ::

         ✅ BDF validation passed
            rows: 25,162   cols: 7
         {'ok': True, 'missing': [], 'extras': [], 'required': ('Test Time / s', 'Voltage / V', 'Current / A'), 'optional': ('Unix Time / s', 'Cycle Count / 1', 'Step Count / 1', 'Ambient Temperature / degC', 'Step Index / 1', 'Charging Capacity / Ah', 'Discharging Capacity / Ah', 'Step Capacity / Ah', 'Net Capacity / Ah', 'Cumulative Capacity / Ah', 'Charging Energy / Wh', 'Discharging Energy / Wh', 'Step Energy / Wh', 'Net Energy / Wh', 'Cumulative Energy / Wh', 'Power / W', 'Internal Resistance / ohm', 'Ambient Pressure / Pa', 'Applied Pressure / Pa', 'Surface Temperature T1 / degC', 'Surface Temperature T2 / degC', 'Surface Temperature T3 / degC', 'Surface Temperature T4 / degC', 'Surface Temperature T5 / degC'), 'n_rows': 25162, 'n_cols': 7, 'time_stats': {'present': True, 'monotonic': True, 'violations': 0, 'min_drop': 0.0, 'first_bad_index': None, 'epsilon': 1.0}}

.. container:: cell code
   :name: 9875c82b

   .. code:: python

      # Validate the file prior to bdf conversion
      report = bdf.validate(filepath, report=True)

   .. container:: output stream stdout

      ::

         Validation failed: SINTEF__LiGrR2032__2024-04-30__25degC__Landt.csv does not look like a BDF artifact (expected .bdf.<ext> or a BDF-style header).
