Get Started
===========

Battery Data Format (BDF) is a canonical schema for cycler time-series data.
The ``bdf`` package reads vendor exports, normalizes them into BDF, validates
and repairs datasets, and produces metadata for sharing.

What BDF standardizes
---------------------

BDF fixes the column labels and units so datasets from different cyclers can be
compared without custom glue code.

Required columns:

- ``Test Time / s``
- ``Voltage / V``
- ``Current / A``

Common recommended columns:

- ``Unix Time / s``
- ``Cycle Count / 1``
- ``Step Count / 1``
- ``Ambient Temperature / degC``

Install
-------

.. code-block:: bash

   pip install batterydf

PyPI distribution name is ``batterydf``; Python import and CLI remain ``bdf``.

Extras (combine as needed, for example ``pip install "batterydf[hvplot]"``):

.. list-table::
   :header-rows: 1
   :widths: 25 55

   * - Extra
     - Adds
   * - ``hvplot``
     - Interactive exploration with Bokeh/HoloViews.
   * - ``polars``
     - Polars support plus the fast NDA backend (requires numpy>=2.2).
   * - ``numpy2``
     - Forces numpy 2.x (recommended for fastnda).
   * - ``dev``
     - Test and lint tooling for contributors.
   * - ``docs``
     - Sphinx docs toolchain.
   * - ``fastnda``
     - Fast NDA backend (requires numpy>=2.2).

Plotly interactive plots and Neware NDA support are included in the base install.

Quickstart notebook
-------------------

Open the rendered notebook:

- :doc:`examples/quickstart`
- :download:`Download the notebook <examples/quickstart.ipynb>`

First steps
-----------

.. code-block:: python

   import bdf

   # read() returns (frame, metadata); the frame is a polars frame
   df, meta = bdf.read("raw_vendor.csv", lazy=False)  # auto-detect and normalize
   df = df.to_pandas()  # the validate/plot helpers operate on pandas
   report = bdf.validate(df, report=True, raise_on_error=False)
   bdf.plot(df, xdata="Test Time / s", ydata=["Voltage / V"], save="plot.png")

Recommended usage
-----------------

Use ``bdf.read`` for the common path (raw vendor files or existing BDF artifacts).
The other functions are for advanced workflows:

- ``bdf.read(..., normalize=False)``: inspect raw vendor columns without normalization.
- ``bdf.normalize``: normalize a DataFrame you already have in memory.
- ``bdf.validate``: validate a DataFrame or BDF artifact without re-reading.
- ``bdf.load`` / ``bdf.save``: read/write BDF artifacts directly as pandas DataFrames.
