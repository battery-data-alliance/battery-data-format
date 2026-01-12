Ingest raw data
===============

Use :func:`bdf.ingest` to convert raw vendor files to BDF artifacts and validate any
existing BDF files in the same folder.

Basic usage
-----------

.. code:: python

   import bdf

   summary = bdf.ingest(
       "data/raw",
       out_dir="data/bdf",
       format="parquet",
       recursive=True,
   )
   summary

Validate-only pass
------------------

If you only want to validate existing BDF artifacts, point to a folder that already
contains BDF files and disable conversion validation:

.. code:: python

   summary = bdf.ingest(
       "data/bdf",
       validate_existing=True,
       validate_converted=False,
   )

