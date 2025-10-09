from __future__ import annotations
from importlib import import_module
from importlib.resources import files
import pkgutil
from typing import Iterable, Type
from .base import CyclerPlugin, REGISTRY

def _import_all_local_modules():
    pkg = __name__
    for mod in pkgutil.iter_modules([str(files(pkg))]):
        if not mod.name.startswith("_"):
            import_module(f"{pkg}.{mod.name}")

_discovered = False
def _ensure_discovery():
    global _discovered
    if not _discovered:
        _import_all_local_modules()
        _discovered = True

def get_plugin_by_id(pid: str) -> Type[CyclerPlugin] | None:
    _ensure_discovery(); return REGISTRY.get(pid)

def all_plugins() -> Iterable[Type[CyclerPlugin]]:
    _ensure_discovery(); return list(REGISTRY.all())
