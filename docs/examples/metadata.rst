Metadata
========

.. container:: cell markdown
   :name: 01323ff6

   .. rubric:: Metadata
      :name: metadata

   BDF writes rich metadata alongside each dataset (as JSON-LD embedded
   in an HTML landing page and/or sidecar files), so data are
   self-describing and web-discoverable.

   .. rubric:: Design Principles
      :name: design-principles

   BDF favors schema.org for maximum interoperability on the semantic
   web (and rich presentation in Dataset Search), while also supporting
   CSVW for precise tabular schemas. Quantity meanings are grounded in
   the BDF application ontology to keep terminology consistent across
   tools and datasets.

   .. rubric:: Content
      :name: content

   BDF metadata covers three main categories:

   #. **Bibliographic (who/what/when).** This includes information about
      what the dataset is and who made it. The purpose of bibliographic
      metadata is to enable proper citations, credit, reproducibility,
      and searching. It features fields like:

      - title, description, keywords
      - creators and contributors
      - date, version, and license
      - provenance

   #. **Content (what's in the table).** This describes the quantities
      in your dataset so tools and search engines can understand them.
      BDF supports descriptions of table quantities using both
      schema.org and csvw markup. This mapping is handled automatically
      by BDF.

   #. **Distribution (where and how to get the file).** This describes
      the actual downloadable artifact(s). It includes files like:

      - file URL(s), media type (e.g., text/csv, application/parquet)
      - size/checksums (optional), content variants (raw/processed)
      - landing page vs. direct download (schema.org/DataDownload)

.. container:: cell code
   :name: 81a1b3c5

   .. code:: python

      import bdf
      from bdf.metadata import Dataset, Creator, DataDownload

.. container:: cell code
   :name: afee3447

   .. code:: python

      # Read the raw source data and display the header
      df = bdf.read("https://zenodo.org/records/17295469/files/FZJ__INR21700__20250606__HPPC__25degC__Digatron.csv")
      df.head()

   .. container:: output execute_result

      ::

            Test Time / s  Voltage / V  Current / A  Step Index / 1  Cycle Count / 1  \
         0          640.0     3.738163     0.000000               4              0.0   
         1         1649.0     3.814422     4.999872               4              0.0   
         2         2647.0     3.818646     4.999872               4              0.0   
         3         3651.0     3.822092     4.999872               4              0.0   
         4         4649.0     3.825094     4.999872               4              0.0   

            Cumulative Capacity / Ah  Charging Capacity / Ah  \
         0                  0.000000                0.000000   
         1                  0.001396                0.001396   
         2                  0.002780                0.002780   
         3                  0.004176                0.004176   
         4                  0.005562                0.005562   

            Discharging Capacity / Ah  Step Capacity / Ah  Net Capacity / Ah  \
         0                        0.0            0.000000           0.000000   
         1                        0.0            0.001396           0.001396   
         2                        0.0            0.002780           0.002780   
         3                        0.0            0.004176           0.004176   
         4                        0.0            0.005562           0.005562   

            Cumulative Energy / Wh  Charging Energy / Wh  Discharging Energy / Wh  \
         0                0.000000              0.000000                      0.0   
         1                0.005320              0.005320                      0.0   
         2                0.010605              0.010605                      0.0   
         3                0.015937              0.015937                      0.0   
         4                0.021237              0.021237                      0.0   

            Step Energy / Wh  Ambient Temperature / degC  \
         0          0.000000                     25.0625   
         1          0.005320                     25.0625   
         2          0.010605                     25.0625   
         3          0.015937                     25.0625   
         4          0.021237                     25.0625   

            Surface Temperature T1 / degC  Unix Time / s  
         0                        25.8125   1.749201e+09  
         1                        25.8125   1.749201e+09  
         2                        25.8125   1.749201e+09  
         3                        25.7500   1.749201e+09  
         4                        25.7500   1.749201e+09  

.. container:: cell code
   :name: 10839663

   .. code:: python

      meta = Dataset(
          title="Digatron INR21700 HPPC",
          creators=[Creator(name="Example Creator", orcid="0000-0002-0000-0010", given_name="Example", family_name="Creator", affiliation="Your Lab")],
          description="HPPC characterization of an INR21700 cell at 25°C on a Digatron cycler.",
          keywords=["li-ion", "inr21700", "hppc"],
          license="CC-BY-4.0",
          url="https://zenodo.org/records/17295469",
          version="1.0.0",
          publication_date="2025-06-06",
      )

      dist = DataDownload(
          url="https://zenodo.org/records/17295469/files/FZJ__INR21700__20250606__HPPC__25degC__Digatron.csv",
          name="Digatron CSV file",
          encoding_format="text/csv",
          description="Primary CSV export from Digatron cycler."
      )

.. container:: cell code
   :name: 5f716000

   .. code:: python

      # Save the metadata to JSON-LD

      meta.save_jsonld(
         "out/metadata.jsonld",
          dataset_uri="https://doi.org/10.5281/zenodo.16994937#digatron-csv-li-ion-hppc",
          identifier="digatron-csv-li-ion-hppc",
          distributions=[dist],
          df=df
      )

   .. container:: output execute_result

      ::

        WindowsPath('out/metadata.jsonld')

.. container:: cell code
   :name: e32cf246

   .. code:: python

      # Save the metadata as rich results html for Google / Semantic Web integration

      meta.save_rich_results_html(
         "out/metadata.html",
          title="Digatron INR21700 HPPC",
          graphify=True,
          dataset_uri="https://doi.org/10.5281/zenodo.16994937#digatron-csv-li-ion-hppc",
          identifier="digatron-csv-li-ion-hppc",
          distributions=[dist],
          df=df
      )

   .. container:: output execute_result

      ::

        WindowsPath('out/metadata.html')
