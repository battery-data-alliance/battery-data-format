# Finding and linking measurement terms

On the `codex/custom-measurements-prototype` branch, BDF offers offline ontology
suggestions for additional columns. Conversion and file validation show one
advisory report; ordinary `read()` and `scan()` calls do not issue reminders.
Already linked columns and standard BDF columns need no suggestion.

## Review and accept

Run `bdf terms experiment.bdf.csv` to inspect a file without changing it. Use
`--json` for a structured report including index provenance and candidates.
For an unlinked `Thickness / mm` column, the report suggests EMMO Thickness
and shows its IRI. Review the definition at that IRI and the measurement's
meaning before selecting it:

```sh
bdf terms experiment.bdf.csv \
  --accept 'Thickness / mm=https://w3id.org/emmo#EMMO_43003c86_9d15_433b_9789_ee2940920656' \
  --save-mappings height-gauge-mappings.json
```

This updates the `.metadata.json` sidecar and preserves existing metadata,
including measurement descriptions. Data bytes are unchanged. If a column has
no description yet, its exact header and stated units form a new
`variable_measured` entry. Bare headers require units in metadata or a profile;
the tool does not infer units from values. Repeat `--accept` for multiple columns.

Reuse explicitly accepted mappings on another file from the same setup:

```sh
bdf terms next-experiment.bdf.csv --mappings height-gauge-mappings.json
bdf convert next-experiment.bdf.csv --to next-experiment.bdf.parquet
bdf validate next-experiment.bdf.parquet
```

A profile contains `format_version: 1`, the source index identity, and a
`mappings` list with `column`, `unit_text`, and `same_as` for each selection.
Profiles require matching headers and equivalent units. Missing columns,
conflicting links, duplicate selections and incompatible units are rejected
before any metadata changes. Profile exports require a new output file.
Existing ontology links are preserved, including links outside this index.
New links accepted through this tool must refer to an indexed term; other
user-supplied links can still be authored directly in metadata.

Suggestions do not make a column mandatory or change its values. An unresolved
column can be retained with a name/unit declaration, or with `--include-unknown`
during conversion. The conversion report identifies undeclared columns excluded
from output. File outputs preserve metadata; stdout carries table data only.

## Where suggestions come from

The package ships [ontology-terms.json](../src/bdf/data/ontology-terms.json), built
from BattINFO's curated property mappings at the commit recorded inside it.
The file is loaded only when an unlinked additional column needs suggestions.
This is separate from BDF's bundled core-column ontology and does not fetch
IRIs, load the full ontology, or require an internet connection.

Matching uses curated property keys and class labels, allowing capitalization,
spaces, underscores and CamelCase variants. It does not make fuzzy guesses.
Multiple candidates remain choices. Definitions are displayed when included in
the index; the current upstream mapping does not supply them, so use the IRI.

BDF adds conservative dimensional hints for thickness, length, width, height,
diameter, mass and volume. These hints are maintained in the generator and are
not constraints supplied by BattINFO's mapping file. Other quantities and
unrecognized units are explicitly marked **unchecked**. Compatible dimensions
do not establish meaning and never trigger conversion.

“No matching term found in this index” describes the search's limited coverage.
The report links to the property reference and the existing community discussion
for further lookup or a proposed addition. Curated property keys can include
specification context, so their suitability for a measured column still needs
review.

Maintainers regenerate the snapshot with
`python scripts/update_ontology_terms.py --ref COMMIT`, using a full commit SHA.
Only this maintainer command downloads source material. `--check` verifies the
generated result; `--source FILE` supports a previously downloaded mapping.
The snapshot records the source commit, mapping version and content hash. Index
updates require review and ship with a BDF release; runtime checks never refresh it.

## Python and executable example

`bdf.ontology_terms.suggest_terms(columns, metadata)` returns the same advisory.
`apply_term_mappings(columns, metadata, mappings)` returns copied metadata with
explicit selections applied; pass that metadata to `bdf.save()` to persist it.
`bdf.validate_df(frame, metadata=metadata)` includes suggestions in its report.

Run `python docs/examples/ontology_terms.py` for the complete CLI workflow on
fabricated sparse thickness readings. All writes go to a temporary directory.
