"""Guards against pyproject.toml drift."""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PRE_COMMIT_CONFIG_PATH = REPO_ROOT / ".pre-commit-config.yaml"


def _load_optional_dependencies() -> dict[str, list[str]]:
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def test_all_extra_includes_every_other_extra():
    """Ensure [all] group contains all other non-dev optional dependencies."""
    extras = _load_optional_dependencies()
    assert "all" in extras

    other_extras = set(extras) - {"all"}

    referenced: set[str] = set()
    for entry in extras["all"]:
        match = re.fullmatch(r"batterydf\[([^\]]+)\]", entry)
        assert match, f"unexpected entry in `all` extra: {entry!r}"
        referenced.update(name.strip() for name in match.group(1).split(","))

    missing = other_extras - referenced
    stale = referenced - other_extras
    assert not missing, f"extras defined but missing from `all`: {missing}"
    assert not stale, f"`all` references extras that no longer exist: {stale}"
