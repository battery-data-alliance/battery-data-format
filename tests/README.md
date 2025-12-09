# Tests layout

- `unit/`: fast, offline tests for core APIs and CLI.
- `integration/`: optional registry/network test (`test_registry_bdf_loading.py`) that may download data; mark/skip in CI as needed.

To run the fast suite:
```
pytest tests/unit
```

To include integration (may require network/cache):
```
pytest tests/integration -m "not network"
```
