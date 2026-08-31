# Governance

Battery Data Format (BDF) is a project of the [Battery Data Alliance](https://github.com/battery-data-alliance),
a Linux Foundation Energy project. This document describes how the BDF
repositories are technically governed. It records practice we already follow;
changes to it go through a pull request like any other change.

## Roles

**Contributors** are everyone who submits issues, pull requests, data samples,
or review comments. No membership is required; see
[CONTRIBUTING.md](CONTRIBUTING.md) to get started.

**Committers** have merge rights on the repository. Committers review
contributions, keep CI green, and are accountable for what merges. The current
committers are listed in [COMMITTERS.md](COMMITTERS.md).

**Project lead** coordinates releases and represents the project toward the
Battery Data Alliance Technical Steering Committee (TSC). The role is listed in
[COMMITTERS.md](COMMITTERS.md).

## Decision making

Decisions are made by lazy consensus in public, in this order of preference:

1. **Pull requests** for anything expressible as a change: code, spec,
   ontology synchronization, documentation, or this file. One committer
   review other than the author is required; substantive changes to the
   column ontology or the public API get discussed with the group first.
2. **GitHub issues** for design questions and anything needing a record.
3. **The mailing list, forum, and monthly sync** for direction-setting:
   release scope, ontology versioning, cross-project coordination.

If consensus does not emerge, the project lead decides after the discussion
has had a reasonable airing; anyone can appeal a decision to the BDA TSC.

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
