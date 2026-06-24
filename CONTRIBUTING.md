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

Install dependencies:
```bash
python -m pip install -e ".[dev,docs]"
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
snapshot (`src/bdf/data/bdf-ontology-snapshot.ttl`) is pinned to an ontology
release, and the term tables in `README.md` (between `BEGIN/END GENERATED`
markers) are generated from it:

- To change a term's name, definition, obligation, or any other metadata,
  open a PR on the **ontology repo** — not here. A daily workflow
  (`sync-ontology.yml`) opens a PR in this repo when a new ontology release
  is published.
- After changing the snapshot locally, regenerate the tables with
  `python scripts/generate_docs.py`. CI fails if the generated regions are
  out of sync (`--check`).
- The required-column set used by `validate_df()` derives from the
  ontology's `obligation` annotations and is pinned by
  `tests/unit/test_spec_ontology_fields.py`; an ontology release that
  changes it must update that test deliberately in the sync PR.

The Supported Plugins page (`docs/plugins.rst`, between `BEGIN/END GENERATED`
markers) is generated from `bdf.plugins.PLUGINS` by the `docs/_ext/generate_plugins_doc.py`
Sphinx extension, which regenerates it automatically on every docs build. No
manual step or CI check is needed -- just change the plugin definition and
build the docs.

## Release workflow (summary)
- Ensure CI is green (lint/type/tests/docs/build).
- Bump version in `pyproject.toml` and update `CHANGELOG.md`.
- Tag and publish (TestPyPI first is recommended).
