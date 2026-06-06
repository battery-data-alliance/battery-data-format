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
```
ruff check .
mypy src/bdf
pytest tests/unit -q
sphinx-build -b html docs docs/_build/html
```

## Pull requests
- Keep PRs focused and include tests for new behavior.
- Update docs/README when changing user-facing APIs.
- Follow the CODE_OF_CONDUCT.

## Release workflow (summary)
- Ensure CI is green (lint/type/tests/docs/build).
- Bump version in `pyproject.toml` and update `CHANGELOG.md`.
- Tag and publish (TestPyPI first is recommended).
