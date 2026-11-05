# Contributing

Thanks for helping improve BDF!

## Ways to help
- Report bugs and feature requests via issues.
- Improve docs and examples.
- Add/extend cycler plugins and tests.

## Setup

Choose one workflow. **uv** is recommended as this project includes a uv.lock file which
fixes all dependencies; venv/pip is standard if you prefer traditional tooling.

### With uv (recommended)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then sync dependencies:
```bash
uv sync --all-extras
```

Install and run pre-commit:
```bash
uv run pre-commit install
uv run pre-commit run --all-files  # Manual run; auto-runs on git commit
```

Run tests and build docs:
```bash
uv run --all-extras pytest
# --all-extras will ensure your test runs in an environment with all optional dependencies
uv run sphinx-build -b html docs docs/_build/html
```

### With venv and pip

Create and activate virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
.venv\Scripts\activate      # Windows
```

Install dependencies (requires pip>=25.1 for `--group`):
```bash
python -m pip install -e . --group dev --group docs
```

Install and run pre-commit:
```bash
pre-commit install
pre-commit run --all-files  # Manual run; auto-runs on git commit
```

Run tests and build docs:
```bash
pytest
sphinx-build -b html docs docs/_build/html
```

### Key difference

- **uv**: Environment auto-managed; `uv run CMD` executes CMD within it.
- **venv/pip**: Manually activate `.venv/` each session; run commands normally.

## Pull requests
- Keep PRs focused and include tests for new behavior.
- Update docs/README when changing user-facing APIs.
- Follow the CODE_OF_CONDUCT.

## Ontology-derived content (do not edit by hand)

The [BDF ontology](https://github.com/battery-data-alliance/battery-data-format-ontology)
is the single source of truth for the canonical quantities. The bundled
snapshot (`src/bdf/data/bdf-ontology/bdf-ontology.ttl`, with its version
in the `VERSION` file beside it) is pinned to one ontology version, and the term tables in `README.md` (between `BEGIN/END GENERATED`
markers) are generated from it:

- To change a term's name, definition, obligation, or any other metadata,
  open a PR on the **ontology repo** — not here. A daily workflow
  (`sync-ontology.yml`) opens a PR in this repo when a new ontology
  is published.
- After changing the snapshot locally, regenerate the tables with
  `python scripts/generate_docs.py`. CI fails if the generated regions are
  out of sync (`--check`).
- The required-column set used by `validate_df()` derives from the
  ontology's `obligation` annotations and is pinned by
  `tests/unit/test_spec_ontology_fields.py`; an ontology version that
  changes it must update that test deliberately in the sync PR.
- The `freshness` marker covers the tests that compare the ontology snapshot
  against upstream. CI runs them once, in the `freshness-tests` job; run them
  locally with `uv run --all-extras pytest -m freshness`.

The Supported Plugins page (`docs/plugins.rst`, between `BEGIN/END GENERATED`
markers) is generated from `bdf.plugins.PLUGINS` by the `docs/_ext/generate_plugins_doc.py`
Sphinx extension, which regenerates it automatically on every docs build. No
manual step or CI check is needed -- just change the plugin definition and
build the docs.

## BattINFO-derived content (do not edit by hand)

[BattINFO](https://github.com/BIG-MAP/BattINFO) is the source of truth for the
JSON schema documents under `src/bdf/data/battinfo/`. The bundle is one
BattINFO schema: `VERSION` names its version (for example `0.8.0rc2`), and
`schemas/` holds the six entity documents and every document that their
`$ref`s reach (nine for `0.8.0rc2`).

- To change a schema, open a PR on the **BattINFO repo**, not here.
- To move the bundle to another BattINFO schema version, run `uv run bdf-update-battinfo VERSION`
  and commit the result. The command needs a version, because BattINFO
  publishes only pre-releases and so has no latest version. It fetches the
  documents at the tag `vVERSION`, checks each against the JSON Schema metaschema,
  and writes `VERSION` and `schemas/`. Review
  the diff: the shipped plugins' metadata rules must still name paths that the
  new BattINFO schema declares, and `tests/unit/test_plugins.py` fails when one does not.
- A `network` test (`test_a_fetched_bundled_release_is_the_bundle`) fetches the
  bundled BattINFO schema again and compares it with the bundle byte for byte, so a
  hand edit or a broken fetch fails CI's `network-tests` job.
- Never edit the documents under `schemas/` by hand.

## Reference artifacts are byte-exact

The files under `docs/examples/reference/` are hashed into `datasets.json`, so `.gitattributes` marks them `-text` and git must never rewrite their line endings. After pulling the change that introduced this, a pre-existing checkout may show all of them as modified and fail the manifest sha test: nothing is wrong with the data - refresh the working copies with `git checkout -- docs/examples/reference`.

## Release workflow (summary)
- Ensure CI is green (lint/type/tests/docs/build).
- Bump version in `pyproject.toml` and update `CHANGELOG.md`.
- Tag the release. A `v*` tag builds and publishes to PyPI from CI.
- Release candidates are published to PyPI as well, as pre-releases.
  `pip` and `uv` skip pre-releases unless asked, so testers opt in with
  `--pre` or an exact pin. TestPyPI is only for rehearsing the workflow.
