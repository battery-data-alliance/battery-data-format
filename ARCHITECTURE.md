# Architecture

How the pieces of Battery Data Format fit together, top to bottom. User-facing
documentation lives at the [docs site](https://battery-data-alliance.github.io/battery-data-format/);
this page is the map for contributors.

## The layers

```
  battery-data-format-ontology  (separate repo, versioned releases)
      │  defines every canonical column: IRI, unit, definition,
      │  synonyms, deprecations (isReplacedBy), derived-column rules
      ▼
  bundled ontology snapshot  (src/bdf/data/, synced by CI at each release)
      │  loaded by spec.py into ColumnOntology: labels, unit conversion,
      │  synonym index, validity rules
      ▼
  plugins  (src/bdf/plugins.py — one Plugin per vendor format)
      │  each plugin pairs:
      │    a table parser   (table_parsers.py: how bytes become a table)
      │    a normalizer     (table_normalizers.py: vendor headers → terms)
      │    a metadata parser(metadata_parsers.py: preambles/sidecars → Metadata)
      │  detection resolves a file to a plugin in three stages:
      │  extension/magic bytes → metadata match → column scoring
      ▼
  io  (io.py: the public API)
      │  read()  → (polars DataFrame, Metadata)
      │  scan()  → (polars LazyFrame, Metadata)   lazy, for large files
      │  save()  → BDF artifact + .metadata.json sidecar
      │  time-scale detection runs on read (fsck model: loud by default,
      │  repair only with reconcile_time=True)
      ▼
  validate / repair  (_validate.py, repair.py)
      │  schema, unit, monotonicity, derived-column consistency checks;
      │  cleaning helpers (fix_time, clean)
      ▼
  cli  (cli.py: ingest, convert, validate, detect, clean, plot, templates)
         composable: stdin/stdout piping, exit codes, --json
```

## Metadata

Metadata follows the same shape as the table pipeline. BattINFO JSON schemas
(bundled under `src/bdf/data/battinfo/`, refreshed from upstream by
`scripts/update_battinfo.py`) generate pydantic models. `read()` returns a
typed `Metadata` object assembled from exactly one source: the artifact's
`.metadata.json` sidecar when present, the vendor file's own metadata
otherwise. `save()` writes the sidecar and refuses ambiguous overwrites, so a
sidecar always describes the last save.

## Design rules that shape everything

- **The ontology is the single source of truth.** Column meaning lives in the
  ontology release, not in Python. The package syncs; it does not define.
- **The table carries measurements only.** Everything else (cell, test,
  dataset, provenance) is metadata in companion structures.
- **Detect loudly, repair only on explicit request.** Data that contradicts
  its own labels raises; flags like `reconcile_time=True` are the only path
  that modifies values.
- **Same kind in, same kind out.** APIs accept polars (eager or lazy) or
  pandas and return the matching kind; internals are polars.
- **Reference data is gated.** Every file in `docs/examples/reference/` must
  pass read-back and derived-column checks in CI, with a shrinking allowlist
  for known-bad samples kept deliberately.

## Repository neighbors

- `battery-data-format-ontology` — the term definitions and releases.
- `bdf-datastore` — reference datastore conventions (contribution/battery
  metadata sidecars) built on this package.
