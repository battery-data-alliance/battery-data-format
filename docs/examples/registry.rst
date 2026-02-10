Registry
========

Build a local metadata registry from JSON-LD sources and query it.

.. code:: python

   import bdf
   from pathlib import Path

   source = "https://github.com/DigiBatt/battery-data/tree/main"
   registry_dir = Path("out/registry")
   summary = bdf.build_registry(source, registry_dir=registry_dir, refresh=False)

   hits = bdf.search("nmc 3.7V 5Ah", registry_dir=registry_dir)
   hits[:3]

   query = """
   PREFIX schema: <https://schema.org/>
   SELECT ?dataset ?title WHERE {
     ?dataset a schema:Dataset ;
              schema:name ?title .
   } LIMIT 5
   """
   bdf.sparql(query, registry_dir=registry_dir)
