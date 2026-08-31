# Community growth

How the Battery Data Alliance intends to grow the contributor and user base
around BDF. Companion to [ROADMAP.md](ROADMAP.md) and
[GOVERNANCE.md](GOVERNANCE.md).

## Where we are

- 5 code contributors across 4 organizations (SINTEF, Imperial College
  London, Empa, AmpLabs), with sustained commit flow through the 0.2.0
  release train (LFX Insights / repo history).
- Adoption beyond the committers, none of it solicited: Arbin ships BDF
  export in MITS; PyProBE v3 builds its column system on the BDF ontology;
  PyBOP adopted BDF-native naming for its parameterisation datasets; Empa
  published a catalysis data format extending the BDF ontology; independent
  tools (Battery-Feature-Lab) and pipelines (Corvus Energy, Neware-to-SQL)
  consume BDF.
- Community surfaces: GitHub, the public forum (bda.discourse.group), a
  monthly open sync, and the LF Energy mailing lists.

## How contributors arrive, and how we widen each path

**Vendor format plugins are the on-ramp.** A parser + normalizer for one
cycler format is self-contained, testable, and immediately useful; our Arbin
.res support arrived exactly this way from a first-time contributor. We
maintain good-first-issue labels for format gaps, keep the plugin contract
documented (ARCHITECTURE.md, the plugins reference page), and credit
contributors in the changelog and release notes.

**Data is a contribution.** Sample files for the test corpus and reference
datasets lower the barrier below code: labs can contribute measurement files
under open licenses and see them become validation fixtures. The reference
gate machinery makes every such contribution durable.

**Release candidates recruit testers.** Each rc ships with a public testing
call (forum + list) asking people to run their own exports through
`bdf validate`; every surprise reported before a final release is a
contributor conversation started.

**Downstream tools are multipliers.** Each tool that reads or writes BDF
(modeling, analysis, pipelines) brings its users to the format. We support
them deliberately: fast review for interoperability issues, no breaking
changes without a migration line in the changelog, and direct engagement
when their conventions and ours can converge (as with PyProBE v3).

**Vendors are courted directly.** The Arbin engagement (BDF export in MITS,
their team auditing against `validate()`) is the template: standing
invitations to cycler vendors to implement export, with the spec, ontology,
and validator as the working interface.

**The committer path is written down.** GOVERNANCE.md documents how
sustained contributors become committers, and inactivity moves committers to
emeritus rather than gatekeeping forever. Target: at least one new committer
per year from outside the current four organizations.

## Path to Early Adoption (outline)

The Early Adoption stage requires, beyond Incubation: continued growth in
commits/committers/organizational diversity; production use by at least two
independent end users; a TSC of at least five members with an elected chair
and regular open meetings; the OpenSSF Best Practices badge at Silver; and a
documented 18-month plan (releases, target users, standards requirements,
contributor expansion, infrastructure needs).

Our outline:

1. **Production users.** Convert existing adoption into documented
   production use: Arbin's MITS export and the first lab or industrial
   pipeline running BDF end-to-end are the nearest candidates.
2. **TSC.** Expand to five-plus members with an elected chair, drawing on
   the contributing organizations and adopters; move the monthly sync to a
   published open-meeting cadence with minutes.
3. **OpenSSF Silver.** The largest deltas from Passing are already in hand
   (architecture documentation, roadmap); close the remainder
   (documented design decisions, signed releases or provenance,
   additional security process items) as a tracked milestone.
4. **18-month plan.** Extend ROADMAP.md's 12-month horizon with release
   timelines, target-user segments (labs, vendors, modeling tools),
   relevant standards interfaces (ontology/IRI infrastructure, BattINFO),
   and infrastructure needs (CI, data hosting via Zenodo, docs).
