# Governance

Battery Data Format (BDF) is a project of the [Battery Data Alliance](https://github.com/battery-data-alliance),
a Linux Foundation Energy project. This document describes how the BDF
repositories are technically governed. It records practice we already follow;
changes to it go through a pull request like any other change.

## Project stage

BDA is at the **Incubation** stage of the [LF Energy project
lifecycle](https://tac.lfenergy.org/process/lifecycle.html), advanced from
Sandbox by TAC vote on 22 September 2026
([lf-energy/tac#57](https://github.com/lf-energy/tac/issues/57)). The path to
the next stage, Early Adoption, is outlined in [GROWTH.md](GROWTH.md).

## Roles

**Contributors** are everyone who submits issues, pull requests, data samples,
or review comments. No membership is required; see
[CONTRIBUTING.md](CONTRIBUTING.md) to get started.

**Committers** have merge rights on the repository. Committers review
contributions, keep CI green, and are accountable for what merges. The current
committers are listed in [COMMITTERS.md](COMMITTERS.md).

**Release manager** is a per-release coordination role a committer takes on
for a given release train; it carries no special authority. Alliance-level
authority rests with the Battery Data Alliance Technical Steering Committee
(TSC), described below.

## Technical Steering Committee

Today the TSC is the committers. The four people listed in
[COMMITTERS.md](COMMITTERS.md) are its whole membership; there is no separate
body and no separate roster. They maintain BDF and its affiliated integrations
equally: the ontology, the datastore and the BattINFO integration are the
project's work, not any one member's, and no area carries a standing owner.

Gabe Hege is **chair** of the TSC and BDA's representative to the LF Energy
Technical Advisory Council.

The Early Adoption stage asks for a TSC of at least five members with an
elected chair and regular open meetings. We are four, and the monthly sync is
the meeting. Expanding the membership and holding a chair election are the
open items, tracked in [GROWTH.md](GROWTH.md) and
[issue #114](https://github.com/battery-data-alliance/battery-data-format/issues/114).

Writing this down as it stands, rather than as we would like it to read, is
deliberate: the gap is the work.

## Decision making

Decisions are made by lazy consensus in public, in this order of preference:

1. **Pull requests** for anything expressible as a change: code, spec,
   ontology synchronization, documentation, or this file. One committer
   review other than the author is required, with one exception: a change
   that cannot alter behavior -- typo and formatting fixes, changelog and
   documentation wording, comment corrections, and dependency bumps that
   only refresh a lockfile -- may merge on green CI without a second
   review. When in doubt, it is not trivial. Substantive changes to the
   column ontology or the public API get discussed with the group first.

   Lazy consensus has a clock. If a pull request has been open for **five
   working days** with green CI and no committer has reviewed it or asked
   for more time, any committer may merge it, including its author. A
   merge under this rule says so in the merge comment, so the absence of
   review is on the record rather than invisible. The window does not
   apply to changes to the column ontology, the public API, or this file,
   which wait for a real review however long that takes.
2. **GitHub issues** for design questions and anything needing a record.
3. **The mailing list, forum, and monthly sync** for direction-setting:
   release scope, ontology versioning, cross-project coordination.

If consensus does not emerge after a reasonable airing, the question goes to
the BDA TSC, whose decision stands. Today the committers and the TSC
substantially overlap; the escalation path exists for when they don't.

## Contributions from outside the committers

A pull request from someone who is not a committer gets a human response
within **five working days**: a review, a question, or a note saying when it
will be looked at. "We are thinking about it" is a valid response; silence is
not. Where two contributors have solved the same problem independently, we say
which approach we are taking and why, and we credit the one we do not merge.

Nothing here promises a merge. It promises an answer.

## Becoming a committer

Sustained, high-quality contribution is the path: multiple merged
contributions, constructive review of others' work, and familiarity with the
spec's compatibility rules. Any current committer can nominate; existing
committers decide by lazy consensus. Committers inactive for a year may be
moved to emeritus status (with thanks, and an easy road back).

## Releases

Releases follow the changelog and versioning rules described in
[CONTRIBUTING.md](CONTRIBUTING.md). Breaking changes are batched into
minor releases before 1.0 and announced on the changelog, the forum, and the
mailing list. The ontology versions independently; the package pins a released
ontology snapshot.

## Code of conduct

The project follows the [Code of Conduct](CODE_OF_CONDUCT.md). Conduct
concerns go to the addresses named there or to the BDA TSC.
