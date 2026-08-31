# Roadmap

What Battery Data Format intends to do, and not do, over the next year.
Living document; the working view is the
[BDA roadmap board](https://github.com/orgs/battery-data-alliance/projects/3).
Dates are intentions, not promises.

## Now: 0.2.0 (release candidate out)

The break-once release: the API frozen around `read()`/`scan()`/`save()` on
polars, ontology 1.3.0 with schedule-scoped accumulator terms, time-scale
detection (fsck model), typed BattINFO metadata with a safe sidecar
round-trip, community parsers (Arbin .res, BioLogic .mpr, Arbin MITS xlsx,
Neware .ndax), a gated canonical reference bundle, and a composable CLI.

## Next: 0.3.0 (target: winter 2026/27)

- **Extension mechanism** for domain columns beyond the core ontology,
  following the pattern piloted by Empa's catalysis-format extension (#37),
  with custom-measurement needs (e.g. swelling) as the driving use cases.
- **EIS conventions documented**: interleaved sweeps in the procedure file,
  sweep identity via step occurrence, terms for the DC operating point and
  excitation amplitude decided with the PyBOP/PyProBE community.
- **Cell/test linkage convention** (#92): how a BDF artifact declares what it
  belongs to, aligned with the metadata schemas (#106).
- **Metadata follow-through**: scorecard engine recomputed from code on main,
  per-cycle quantity files, streaming-save validation cost (#108), partial
  artifact on failed sink (#104).
- **Reference data at scale**: hybrid storage — a small in-repo smoke set for
  offline CI, the full artifact set published as a versioned Zenodo record at
  each tag (no committed artifact over 20 MB).

## Toward 1.0 (when the criteria are met, not a date)

- A written **deprecation policy**: announce, redirect via the ontology's
  `isReplacedBy`, remove after N releases.
- Two consecutive minor releases without breaking API changes.
- Vendor-side adoption validated against the spec (Arbin MITS export audit
  as the first case).
- Formal validation levels formalized with the working group.

## What BDF does not do

- **Not an analysis library.** Feature extraction, modeling, and plotting
  beyond quick-look belong to downstream tools (PyProBE, PyBOP,
  Battery-Feature-Lab) that read BDF.
- **Not an instrument-control interface.** BDF standardizes exported data;
  controlling cyclers is out of scope.
- **Not a protocol-definition standard.** Test procedures are a companion
  effort (the standard cycler protocol conversation, aurora-unicycler); BDF
  records what happened, links to protocol metadata, and stays out of
  defining it.
- **Not a database or a hosted service.** BDF is files and a library;
  datastores are built on top of it.
