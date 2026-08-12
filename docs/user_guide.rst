User Guide
==========

Input formats
-------------

``bdf.read`` auto-detects cycler exports. You can also force a plugin:

.. code-block:: python

   import bdf
   df, meta = bdf.read("raw_vendor.csv", plugin="neware_csv")

The registered plugin ids are the keys of ``bdf.plugins.PLUGINS`` (use
``bdf.plugins.list_sources()`` to list them). See :doc:`plugins` for the full
catalog of every plugin, the file types it handles, its metadata parser, and
its column synonyms.

Timezone handling
------------------

Vendor formats without an embedded UTC offset (Arbin, Maccor, Neware, Novonix,
LANDT) have their datetime columns parsed and converted to ``Unix Time / s``
assuming UTC by default. Pass ``tz`` (an IANA zone name, e.g.
``"Europe/London"``) to ``bdf.read`` or ``bdf.normalize`` if the data was
recorded in a different timezone:

.. code-block:: python

   df, meta = bdf.read("raw_vendor.csv", tz="Europe/London")

Leaving ``tz`` at its default (``"UTC"``) emits a ``UserWarning`` when a naive
format is in play, so the assumption is never silent. Formats that already
embed an offset (e.g. Digatron's ``%:z``-suffixed timestamps) ignore ``tz``
entirely — the embedded offset is authoritative.

Workflows
---------

.. code-block:: python

   import bdf

   df, meta = bdf.read("raw_vendor.csv")
   df = df.to_pandas()  # clean/plot operate on pandas
   df_clean, rep = bdf.clean(df, time_fix="segment", outlier="none")
   bdf.plot(df_clean, xdata="Test Time / s", ydata=["Voltage / V"])

Plotly interactive plots require ``batterydf[plot]``; Bokeh/HoloViews
backends require ``batterydf[hvplot]``.

Recommended usage
-----------------

Use ``bdf.read`` for most workflows. The lower-level functions are for advanced
cases:

- ``bdf.read(..., normalize=False)``: read vendor data without normalization.
- ``bdf.normalize``: normalize an in-memory DataFrame.
- ``bdf.validate``: validate a DataFrame or BDF artifact without re-reading.

For parse-only workflows:

.. code-block:: python

   df_raw, meta = bdf.read("raw_vendor.csv", normalize=False, validate=False)

For collections:

.. code-block:: python

   summary = bdf.ingest("data/raw", out_dir="data/bdf", format="parquet")

CLI equivalent:

.. code-block:: bash

   bdf ingest data/raw --out-dir data/bdf --format parquet

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
