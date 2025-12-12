Command Line Interface
======================

The ``bdf`` CLI wraps common workflows. Examples:

.. code-block:: bash

   bdf --help
   bdf validate data/sample.bdf.csv
   bdf clean data/sample.bdf.csv --out cleaned.bdf.csv --assume-bdf
   bdf convert raw/vendor.csv --to output.bdf.csv
   bdf detect raw/vendor.csv
   bdf plot data/sample.bdf.csv --assume-bdf --save plot.png
   bdf meta-jsonld data/sample.bdf.csv --title "My dataset" --description "..." --creator "Alice|0000-0000-0000-0000|Org"

Commands
--------

- ``meta-jsonld``: build JSON-LD/CSVW metadata sidecar for a dataset.
- ``clean``: fix non-monotonic time and optional outliers; outputs BDF CSV.
- ``validate``: check BDF schema and basic sanity; JSON output optional.
- ``detect``: identify the most likely cycler/plugin for a raw file.
- ``convert``: parse raw vendor data and emit normalized BDF CSV.
- ``plot``: plot BDF data (saves image, optional GUI display).

Notes
-----

- Use ``--assume-bdf`` to skip raw detection when you already have BDF-normalized data.
- ``meta-jsonld`` accepts multiple ``--creator "Name|ORCID|Affiliation"`` entries and optional ``--related`` relationships.
- Set ``MPLBACKEND=Agg`` in headless environments to avoid GUI popups when plotting.
