User Guide
==========

Input formats
-------------

``bdf.read`` auto-detects cycler exports. You can also force a plugin:

.. code-block:: python

   import bdf
   df = bdf.read("raw_vendor.csv", plugin="neware-csv")

Supported plugins:

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Plugin id
     - File types
     - Notes
   * - ``basytec-txt``
     - ``.txt``, ``.dat``
     - Basytec result files.
   * - ``biologic-mpt``
     - ``.mpt``
     - BioLogic MPT exports.
   * - ``digatron-csv``
     - ``.csv``
     - Digatron CSV exports (including unit-row layouts).
   * - ``landt-csv``
     - ``.csv``
     - Landt CSV with snake_case headers.
   * - ``landt-txt``
     - ``.txt``
     - Landt text exports.
   * - ``matlab-mat``
     - ``.mat``
     - Requires a sidecar mapping file.
   * - ``neware-csv``
     - ``.csv``
     - Neware/BTS CSV exports.
   * - ``neware-nda``
     - ``.nda``, ``.ndax``
     - Neware NDA export (included by default).
   * - ``neware-nda-fast``
     - ``.nda``, ``.ndax``
     - Fast NDA backend (requires ``fastnda``).
   * - ``novonix-csv``
     - ``.csv``
     - Novonix UHPC CSV with ``[Data]`` section.

Workflows
---------

.. code-block:: python

   import bdf

   df = bdf.read("raw_vendor.csv")
   df_clean, rep = bdf.clean(df, time_fix="segment", outlier="none")
   bdf.plot(df_clean, xdata="Test Time / s", ydata=["Voltage / V"])

Plotly interactive plots are included in the base install; Bokeh/HoloViews
backends require ``bdf[hvplot]``.

Recommended usage
-----------------

Use ``bdf.read`` for most workflows. The lower-level functions are for advanced
cases:

- ``bdf.parse``: read vendor data without normalization.
- ``bdf.normalize``: normalize an in-memory DataFrame.
- ``bdf.validate``: validate a DataFrame or BDF artifact without re-reading.

For parse-only workflows:

.. code-block:: python

   df_raw = bdf.parse("raw_vendor.csv")

For collections:

.. code-block:: python

   summary = bdf.ingest("data/raw", out_dir="data/bdf", format="parquet")

For repositories with multiple collections:

.. code-block:: python

   summary = bdf.ingest("data/repos", layout="nested", discover_collections=True)

Metadata
--------

BDF emits JSON-LD metadata for datasets and distributions.

.. code-block:: python

   from bdf.metadata import Dataset, Creator, DataDownload

   meta = Dataset(
       title="Example dataset",
       creators=[Creator(name="Example Creator")],
       description="Short description of the dataset.",
   )

   dist = DataDownload(
       url="https://example.org/data.csv",
       name="Raw CSV export",
       encoding_format="text/csv",
   )

   meta.save_jsonld("out/metadata.jsonld", distributions=[dist])

Registry
--------

Aggregate JSON-LD metadata into a local registry for search and SPARQL queries.

.. code-block:: python

   import bdf

   bdf.build_registry(["/path/to/metadata-root"], registry_dir="~/.bdf/registry")
   hits = bdf.search("nmc 3.7V 5Ah", registry_dir="~/.bdf/registry")
