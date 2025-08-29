# src/bdf/detect.py (excerpt)
from importlib import metadata
from pathlib import Path

def _iter_plugins():
    # try entry points first
    eps = list(metadata.entry_points().select(group="bdf.cyclers"))
    if eps:
        for ep in eps:
            yield ep.load()()
        return
    # fallback: built-ins for dev/tests
    try:
        from .cyclers import get_builtin_plugins
        for cls in get_builtin_plugins():
            yield cls()
    except Exception:
        return

def detect(path: str | Path):
    p = Path(path)
    head = p.read_bytes()[:4096]
    best = None
    for plugin in _iter_plugins():
        s = plugin.sniff(p, head)
        if not best or s.confidence > best.confidence:
            best = s
    if not best:
        raise RuntimeError("No cycler plugin could detect this file")
    return best

def load_plugin(path: str | Path, as_: str | None = None):
    if as_:
        for pl in _iter_plugins():
            if pl.id == as_:
                return pl
        raise ValueError(f"Unknown plugin id: {as_}")
    sr = detect(path)
    for pl in _iter_plugins():
        if pl.id == sr.id:
            return pl
    raise RuntimeError("Detection succeeded but plugin instance not found")
