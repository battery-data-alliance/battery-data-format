# Custom measurements: prototype proposal

**Status:** target 0.2.0 final through a subsequent release candidate, subject to
agreement with Simon and Graham and passing release checks. This feature is not
in the published 0.2.0rc2. Consider 0.3.0 only if it cannot be ready for 0.2.0.

[Phil's proposal](https://bda.discourse.group/t/adding-optionality-for-custom-measurement-integration/56)
describes pouch thickness measured intermittently during a 1,000-cycle test.
These repeated observations belong in a sparse column, distinct from baseline
cell dimensions. Thickness already maps to EMMO's `Thickness` in
[BattINFO's property reference](https://big-map.github.io/BattINFO/dev/pages/property-reference.html).
Here, "custom" means outside BDF's current column list, not outside the ontology.
The optional `.metadata.json` sidecar can reuse that term:

```json
{
  "battinfo_dataset": {
    "dataset": {
      "variable_measured": [{
        "name": "Thickness",
        "unit_text": "mm",
        "same_as": "https://w3id.org/emmo#EMMO_43003c86_9d15_433b_9789_ee2940920656",
        "description": "Pouch thickness measured with a parallel plate height gauge at 100% SOC every 100 cycles."
      }]
    }
  }
}
```

Custom declarations require nonblank `name` and `unit_text`. `description` and
`same_as` (an ontology IRI) are optional; links are stored without fetching them.
Reuse known terms; allowing descriptions without a suitable term is a separate
extension question, not a requirement established by Phil's thickness example.

- **Binding:** `Thickness` plus `mm` identifies `Thickness / mm`; alternatively,
  `name` can be the exact header. If both exact and composed headers exist, the
  binding is ambiguous. Missing columns, duplicates, and conflicting units are
  errors. Header units must agree with declarations; no conversion occurs.
- **Sparse readings:** values belong to their row's test time and cycle. Writers
  supply that alignment. Null means no observation; nothing is filled or
  interpolated. Descriptions record methods and conditions.
- **Preservation:** `read()` and `scan()` load metadata before normalization and
  retain declared custom columns by default. `include_unknown=True` additionally
  retains undeclared extras. `save(..., metadata=metadata)` checks bindings before
  writing and preserves declarations. Standard BDF descriptors retain normal
  behavior and cannot redefine core quantities.

Invalid custom bindings raise `BDFMetadataError`. `validate=False` warns and skips
invalid bindings, so their columns are not guaranteed retention. Metadata remains
optional; saving does not invent descriptions.

**Compatibility decision before release:** previously descriptive custom entries
now control retention and can reject a read. Review existing sidecars before
adopting this default, or choose explicit activation.

**Prototype limitation:** Parquet preserves numeric types; CSV custom cells remain
text with nulls preserved. Unit declarations do not trigger casts. Automatic type
inference remains a discussion choice, deferred from this prototype.

[Issue #37](https://github.com/battery-data-alliance/battery-data-format/issues/37)
addresses broader extension schemas. Ontology loading, automatic interpretation
or conversion, and generated BattINFO schema changes remain outside this work.

**Evidence:** the [executable example](../examples/custom_measurements.py),
[CSV](../examples/custom_measurements/sparse_thickness.bdf.csv), and
[sidecar](../examples/custom_measurements/sparse_thickness.bdf.metadata.json)
demonstrate preservation. All values are fabricated, not Microsoft measurements;
new experimental data is unnecessary for proposal review.

Run `python docs/examples/custom_measurements.py` with this branch installed.
Output is temporary unless `--output-dir PATH` is supplied.

**Verification (Python 3.12):** 49 [acceptance checks](../../tests/unit/test_custom_measurements.py)
pass; the broader offline suite reports 1,191 passed, 37 skipped and 139 expected
failures (network/freshness/notebook cases excluded). Formatting passes; docs build
with existing warnings. Release gates remain red: upstream BattINFO freshness
(two unchanged bundled schemas differ) and pre-existing type errors reproduced
on main. These are not changes to the prototype's data-preservation behavior.
