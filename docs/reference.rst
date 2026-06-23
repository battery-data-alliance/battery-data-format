Reference
=========

Command Line Interface
----------------------

.. code-block:: bash

   bdf --help
   bdf validate data/sample.bdf.csv
   bdf clean data/sample.bdf.csv --out cleaned.bdf.csv --assume-bdf
   bdf convert raw/vendor.csv --to output.bdf.csv
   bdf detect raw/vendor.csv
   bdf plot data/sample.bdf.csv --assume-bdf --save plot.png
   bdf meta-jsonld data/sample.bdf.csv --title "My dataset" --description "..." --creator "Name|ORCID|Affiliation"

Python API
----------

.. autosummary::
   :toctree: _autosummary
   :recursive:

   bdf.read
   bdf.load
   bdf.save
   bdf.normalize
   bdf.validate
   bdf.clean
   bdf.plot
   bdf.explore
   bdf.detect
   bdf.ingest
   bdf.datasets
   bdf.load_registry
   bdf.get_entry
   bdf.build_registry
   bdf.search
   bdf.sparql
