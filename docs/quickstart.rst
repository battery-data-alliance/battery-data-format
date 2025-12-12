Quickstart
==========

Install
-------

.. code-block:: bash

   pip install bdf[viz,arrow,units]

Read and validate a dataset
---------------------------

.. code-block:: python

   import bdf
   df = bdf.read("path/to/file.bdf.csv")   # or raw vendor file; plugin auto-detected
   report = bdf.validate(df, report=True, raise_on_error=False)

Clean non-monotonic time / outliers
-----------------------------------

.. code-block:: python

   from bdf import clean_bdf
   df2, rep = clean_bdf(df, time_fix="segment", outlier="none")
   print(rep)

Plot
----

.. code-block:: python

   import bdf
   fig = bdf.plot(df2, xdata="Test Time / s", ydata=["Voltage / V"], save="plot.png")

CLI
---

.. code-block:: bash

   bdf validate path/to/file.bdf.csv
   bdf clean path/to/raw.csv --out cleaned.bdf.csv
   bdf convert path/to/raw.csv --to output.bdf.csv
   bdf plot path/to/file.bdf.csv --save plot.png --show False
   bdf meta-jsonld path/to/file.bdf.csv --title "My Dataset" --description "..." --creator "Name|ORCID|Affiliation"

