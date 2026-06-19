from __future__ import annotations

import pkgutil
from collections.abc import Iterable
from importlib import import_module
from importlib.resources import files

from .base import REGISTRY, CyclerPlugin


def _import_all_local_modules() -> None:
    pkg = __name__
    for mod in pkgutil.iter_modules([str(files(pkg))]):
        if not mod.name.startswith("_"):
            import_module(f"{pkg}.{mod.name}")


_discovered = False


def _ensure_discovery() -> None:
    global _discovered
    if not _discovered:
        _import_all_local_modules()
        _discovered = True


def get_plugin_by_id(pid: str) -> type[CyclerPlugin] | None:
    _ensure_discovery()
    return REGISTRY.get(pid)


def all_plugins() -> Iterable[type[CyclerPlugin]]:
    _ensure_discovery()
    return list(REGISTRY.all())
