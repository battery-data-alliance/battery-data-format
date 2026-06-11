# Contributing

Thanks for helping improve BDF!

## Ways to help
- Report bugs and feature requests via issues.
- Improve docs and examples.
- Add/extend cycler plugins and tests.

## Setup

Install with [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended):
```
uv sync --all-extras
```

Or with venv and pip:
```
python -m venv .venv
.venv\Scripts\activate  # Windows
python -m pip install -e .[dev,docs]
```

Run checks locally:

Install pre-commit hooks:
```
pre-commit install
```

Then pre-commit automatically runs on `git commit`. Run manually:
```
pre-commit run --all-files
```

Run tests and build docs:
```
pytest tests/unit -q
sphinx-build -b html docs docs/_build/html
```

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

## Release workflow (summary)
- Ensure CI is green (lint/type/tests/docs/build).
- Bump version in `pyproject.toml` and update `CHANGELOG.md`.
- Tag and publish (TestPyPI first is recommended).
