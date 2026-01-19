from __future__ import annotations

import shutil
import warnings

# mypy: ignore-errors
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

# light imports that never cause cycles
from .detect import detect as _detect, list_plugins as _list_plugins, load_plugin
from .normalize import guess_plugin_by_columns, normalize_columns
from .repair import CleanReport, clean  # public cleaning helpers
from .validate import BDFValidationError, validate_df  # prints report if asked; warns on non-monotonic time

__all__ = [
    # core I/O
    "read", "parse", "normalize", "validate", "detect", "detect_cycler", "plugins",
    # datasets helpers
    "datasets", "load_registry", "get_entry",
    # registry LD helpers
    "ingest_sources", "search", "sparql",
    # harvesting helpers
    "harvest", "crawl",
    # cleaning
    "clean", "CleanReport",
    # viz
    "plot", "explore", "ingest",
    # version
    "__version__",
    # errors
    "BDFValidationError",
]

# Optional version
try:
    from importlib.metadata import version as _pkg_version  # type: ignore
    __version__ = _pkg_version("bdf")
except Exception:
    __version__ = "0.0.0-dev"


# Keep a handle to the original in case you want to restore it later
_default_formatwarning = warnings.formatwarning

def _bdf_short_formatwarning(message, category, filename, lineno, line=None):
    """
    Render warnings without absolute paths. If the warning originates inside
    the bdf package, just show 'bdf.<module>:<lineno>'; otherwise show a short
    filename. Message text remains unchanged.
    """
    try:
        p = Path(filename).resolve()
        # Heuristic: if file path contains '/bdf/' (or '\bdf\') treat it as our package
        fp = str(p).replace("\\", "/")
        if "/bdf/" in fp or fp.endswith("/bdf/__init__.py"):
            # Build a dotted module-ish label
            try:
                # relative to the package root
                pkg_root = Path(__file__).resolve().parent
                rel = p.relative_to(pkg_root)
                mod = "bdf." + ".".join(rel.with_suffix("").parts)
            except Exception:
                mod = "bdf"
            where = f"{mod}:{lineno}"
        else:
            # External warnings: keep only the basename to avoid leaking user paths
            where = f"{p.name}:{lineno}"
    except Exception:
        where = "<unknown>"

    return f"{category.__name__} [{where}]: {message}\n"

# Install the formatter
warnings.formatwarning = _bdf_short_formatwarning


# -------------------------------
# small helpers
# -------------------------------
def _is_url(x: str) -> bool:
    try:
        u = urlparse(str(x))
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def _resolve_source(
    source: str | Path,
    *,
    registry_path: str | Path | None = None,
) -> tuple[Path, str | None]:
    """
    Return a local Path for the source and an optional plugin hint.
    Source may be: local path, http(s) URL, or dataset id from the registry.
    """
    s = str(source)

    # 1) existing file path
    p = Path(s)
    if p.exists():
        return p, None

    # 2) URL → cache it
    if _is_url(s):
        from .fetch import fetch_url  # lazy
        path = fetch_url(s)
        return path, None

    # 3) dataset id from registry
    from ._registry import get_entry as _get_entry, load_registry as _load_registry  # lazy
    reg = _load_registry(registry_path)
    entry = _get_entry(reg, s)  # raises if not found/ambiguous
    url = entry["url"]
    plugin_hint = entry.get("plugin")
    sha256 = entry.get("sha256")
    filename = entry.get("filename")

    from .fetch import fetch_url  # lazy
    path = fetch_url(url, sha256=sha256, filename=filename)
    return path, plugin_hint


# -------------------------------
# public API
# -------------------------------
def read(
    source: str | Path,
    plugin: str | None = None,
    validate: bool = True,
    include_optional: bool = True,
    registry_path: str | Path | None = None,
) -> pd.DataFrame:
    """
    Universal reader → BDF DataFrame (strict columns).
      - source: local path, http(s) URL, or dataset id (from datasets.json)
      - plugin: force a specific cycler plugin id (optional)
      - validate: run BDF validator (prints warnings/report if configured in validate_df)
    """
    local_path, plugin_hint = _resolve_source(source, registry_path=registry_path)
    if _looks_like_bdf_artifact(local_path):
        from .io import load as _load_bdf  # lazy import
        df = _load_bdf(local_path)
        if validate:
            validate_df(df)
        return df
    plg = load_plugin(local_path, plugin_id=(plugin or plugin_hint))
    df_raw = plg.parse(local_path)
    df_raw = plg.augment(df_raw)
    try:
        df = normalize_columns(df_raw, plugin=plg, strict=True, include_optional=include_optional)
    except ValueError:
        if plugin is not None:
            raise
        alt = guess_plugin_by_columns(df_raw, current_id=getattr(plg, "id", None))
        if not alt:
            raise
        if getattr(alt, "id", None) != getattr(plg, "id", None):
            warnings.warn(
                f"Normalization failed with plugin '{getattr(plg, 'id', '?')}', retrying with column-based guess '{getattr(alt, 'id', '?')}'."
            )
        plg = alt
        df_raw = plg.parse(local_path)
        df_raw = plg.augment(df_raw)
        df = normalize_columns(df_raw, plugin=plg, strict=True, include_optional=include_optional)
    if hasattr(plg, "fixup"):
        df = plg.fixup(df)
    if validate:
        validate_df(df)
    return df



def parse(source: str | Path, plugin: str | None = None, registry_path: str | Path | None = None) -> pd.DataFrame:
    """Parse vendor file only (no normalization/validation)."""
    local_path, plugin_hint = _resolve_source(source, registry_path=registry_path)
    plg = load_plugin(local_path, plugin_id=(plugin or plugin_hint))
    return plg.parse(local_path)


def normalize(df: pd.DataFrame, plugin: str | None = None) -> pd.DataFrame:
    """
    Normalize a DataFrame to canonical BDF columns.
    If plugin id is provided, the plugin's local synonyms are applied too.
    """
    plg = None
    if plugin:
        from .data_sources import get_plugin_by_id  # lazy
        cls = get_plugin_by_id(plugin)
        if cls:
            plg = cls()
    return normalize_columns(df, plugin=plg, strict=True)


def _is_csv(path: Path) -> bool:
    s = "".join(path.suffixes).lower()
    return s.endswith(".csv") or s.endswith(".bdf.csv")

def _csv_header_has_bdf_required(path: Path) -> bool:
    """Quickly check if first row contains required BDF columns."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            header = f.readline().strip()
    except Exception:
        return False
    cols = [c.strip() for c in header.split(",")]
    # import lazily to avoid cycles
    from .normalize import REQUIRED
    have = 0
    for req in REQUIRED:
        if any(req.lower() == c.lower() for c in cols):
            have += 1
    return have == len(REQUIRED)

def _looks_like_bdf_artifact(path: Path) -> bool:
    """Return True if filename + header suggest this is a BDF file we should try to load."""
    sfx = "".join(path.suffixes).lower()
    # Parquet/Feather/JSON: accept outright if extension matches
    if sfx.endswith(".parquet") or sfx.endswith(".bdf.parquet"):
        return True
    if sfx.endswith(".feather") or sfx.endswith(".bdf.feather"):
        return True
    if sfx.endswith(".json") or sfx.endswith(".bdf.json"):
        return True
    # CSV: require either .bdf.csv OR BDF header row with required columns
    if _is_csv(path):
        if ".bdf.csv" in sfx:
            return True
        return _csv_header_has_bdf_required(path)
    return False


# src/bdf/__init__.py (validate)
# src/bdf/__init__.py  (replace the existing validate with this)

def validate(
    obj,
    *,
    report: bool = False,
    raise_on_error: bool = False,   # <- default False so notebooks don’t crash
    registry_path: str | Path | None = None,
):
    """
    Validate a BDF DataFrame, a local file path, an HTTP/HTTPS URL, or a dataset id.

    Behavior:
      - DataFrame: validate as-is (no transformations).
      - Path/URL/id: only treated as a *BDF artifact* (strict). We do NOT vendor-parse
        or normalize here. If it doesn’t look like BDF, you’ll get an 'ok=False' report.

    Returns:
      dict report with at least:
        {"ok": True, "issues": [...]}   or   {"ok": False, "kind": "...", "detail": "..."}
    """
    # small local helpers (kept inside to avoid extra imports at module load time)
    def _bad_report(kind: str, detail: str, **extra):
        r = {"ok": False, "kind": kind, "detail": detail}
        if extra:
            r.update(extra)
        if report:
            print(f"Validation failed: {detail}")
        if raise_on_error:
            from .validate import BDFValidationError
            raise BDFValidationError(detail)
        return r

    # Direct DataFrame path
    if isinstance(obj, pd.DataFrame):
        from .validate import validate_df
        return validate_df(obj, report=report, raise_on_error=raise_on_error)

    # Resolve path/URL/registry id to a local path
    if isinstance(obj, (str, Path)):
        from .__init__ import _resolve_source  # local helper already in your package
        local_path, _ = _resolve_source(obj, registry_path=registry_path)
        p = Path(local_path)
        fname = p.name

        # Only attempt to load files that look like BDF artifacts
        def _looks_like_bdf_artifact(path: Path) -> bool:
            # quick filename hint: *.bdf.csv, *.bdf.parquet, *.bdf.feather, *.bdf.json(.gz)
            name_lc = path.name.lower()
            if any(name_lc.endswith(suf) for suf in (
                ".bdf.csv", ".bdf.csv.gz",
                ".bdf.parquet",
                ".bdf.feather",
                ".bdf.json", ".bdf.json.gz",
            )):
                return True
            # header sniff for CSV only (cheap and safe)
            if name_lc.endswith(".csv") or name_lc.endswith(".csv.gz"):
                try:
                    with (gzip.open(path, "rt") if name_lc.endswith(".gz") else open(path, encoding="utf-8", errors="ignore")) as f:
                        head = "".join([f.readline() for _ in range(2)]).lower()
                    # must contain all required labels (case-insensitive)
                    req = {"test time / s", "voltage / v", "current / a"}
                    header_line = head.splitlines()[0] if head else ""
                    return all(r in header_line for r in req)
                except Exception:
                    return False
            return False

        # Optional gzip import for header sniff
        import gzip as _maybe_gzip  # safe alias
        gzip = _maybe_gzip

        if not _looks_like_bdf_artifact(p):
            return _bad_report(
                kind="not_bdf_artifact",
                detail=f"{fname} does not look like a BDF artifact (expected .bdf.<ext> or a BDF-style header).",
                file=fname,
            )

        # Try to load with strict BDF IO (no transformations)
        try:
            from .io import load as _load_bdf  # strict loader for BDF CSV/Parquet/Feather/JSON
            df = _load_bdf(p)
        except Exception as e:
            return _bad_report(
                kind="io_error",
                detail=f"Failed to load BDF artifact {fname}: {e}",
                file=fname,
            )

        # Validate columns/units only; do NOT normalize or modify
        from .validate import validate_df
        return validate_df(df, report=report, raise_on_error=raise_on_error)

    # Anything else: wrong type
    return _bad_report(kind="type_error", detail="validate() expects a pandas DataFrame, a file path (str/Path), a URL, or a dataset id.")


def detect(path: str | Path):
    """Return SniffResult with the best-matching plugin and confidence."""
    return _detect(Path(path))


# Backwards-friendly alias used by CLI
detect_cycler = detect


def plugins() -> list[str]:
    """List available plugin ids."""
    return _list_plugins()


# ----- dataset helpers (lazy to avoid cycles) -----
def datasets(registry_path: str | Path | None = None) -> list[str]:
    """Return dataset IDs from the registry."""
    from ._registry import list_datasets as _list_datasets, load_registry as _load_registry  # lazy
    reg = _load_registry(registry_path)
    return _list_datasets(reg)


def load_registry(path: str | Path | None = None):
    from ._registry import load_registry as _load_registry  # lazy
    return _load_registry(path)


def get_entry(reg, entry_id: str):
    from ._registry import get_entry as _get_entry  # lazy
    return _get_entry(reg, entry_id)


def ingest_sources(
    sources: str | list[str],
    registry_dir: str | Path | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    from .registry_ld import ingest_sources as _ingest_sources  # lazy
    return _ingest_sources(sources, registry_dir=registry_dir, refresh=refresh)


def search(query: str, registry_dir: str | Path | None = None, limit: int = 50):
    from .registry_ld import search as _search  # lazy
    return _search(query, registry_dir=registry_dir, limit=limit)


def sparql(query: str, registry_dir: str | Path | None = None):
    from .registry_ld import sparql as _sparql  # lazy
    return _sparql(query, registry_dir=registry_dir)


def harvest(
    root: str | Path,
    *,
    layout: str = "nested",
    format: str = "parquet",
    recursive: bool = False,
    validate_existing: bool = True,
    validate_converted: bool = True,
    include_optional: bool = True,
    plugin: str | None = None,
    incremental: bool = True,
    force: bool = False,
    raise_on_error: bool = False,
):
    import importlib
    _self = harvest
    _harvest_mod = importlib.import_module(".harvest", __package__)
    # Restore the public function after module import to avoid shadowing.
    globals()["harvest"] = _self
    return _harvest_mod.harvest(
        root,
        layout=layout,
        format=format,
        recursive=recursive,
        validate_existing=validate_existing,
        validate_converted=validate_converted,
        include_optional=include_optional,
        plugin=plugin,
        incremental=incremental,
        force=force,
        raise_on_error=raise_on_error,
    )


def crawl(
    sources: str | list[str],
    *,
    registry_dir: str | Path | None = None,
    refresh: bool = False,
):
    from .registry_ld import crawl as _crawl  # lazy
    return _crawl(sources, registry_dir=registry_dir, refresh=refresh)


def plot(*args, **kwargs):
    """
    Forward to bdf.visualize.plot(...).

    Example:
        bdf.plot(df, xdata="Test Time / s", ydata="Voltage / V", yydata="Current / A",
                 xunit="h", yyunit="mA", title="Voltage vs Time", show=True)
    """
    try:
        from .visualize import plot as _plot
    except Exception as e:
        raise RuntimeError(
            "bdf.plot() requires the visualization module (matplotlib). "
            "Ensure matplotlib is installed."
        ) from e
    return _plot(*args, **kwargs)


def explore(*args, **kwargs):
    """
    Forward to bdf._explore.explore(...).

    Example:
        bdf.explore(df, xdata="Test Time / s", ydata=["Voltage / V"], backend="bokeh")
    """
    try:
        from ._explore import explore as _explore
    except Exception as e:
        raise RuntimeError("bdf.explore() is unavailable.") from e
    return _explore(*args, **kwargs)


def ingest(
    source: str | Path,
    *,
    out_dir: str | Path | None = None,
    format: str = "parquet",
    layout: str = "flat",
    battery_metadata: str = "embedded",
    recursive: bool = True,
    validate_existing: bool = True,
    validate_converted: bool = True,
    include_optional: bool = True,
    plugin: str | None = None,
    incremental: bool = True,
    force: bool = False,
    raise_on_error: bool = False,
):
    """
    Convert raw vendor files to BDF and validate existing BDF artifacts.

    - source: file or directory
    - format: "parquet" (default) or "csv"
    - layout: "flat" (default) or "nested"
        * flat: convert into out_dir/source and emit one collection metadata file
        * nested: convert into data/ under out_dir/source, emit root dataset metadata,
          and emit per-cell metadata.jsonld folders that describe only the battery
    - battery_metadata: "embedded" (default) or "separate" for flat layout
    - out_dir: optional output root for converted files (defaults to source_dir)
    - validate_existing: validate files that already look like BDF
    - validate_converted: validate after conversion
    - plugin: force a specific plugin id for raw files
    - incremental: skip previously processed files when unchanged
    - force: reprocess even if a file looks unchanged

    Returns a summary dict with converted/validated/failed entries.
    Metadata generation uses collection.json/person.json, and nested layout requires battery.json.
    """
    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(p)

    fmt = format.lower().strip()
    if fmt not in {"parquet", "csv"}:
        raise ValueError("format must be 'parquet' or 'csv'")

    layout_mode = layout.lower().strip()
    if layout_mode not in {"flat", "nested"}:
        raise ValueError("layout must be 'flat' or 'nested'")

    battery_mode = battery_metadata.lower().strip()
    if battery_mode not in {"embedded", "separate"}:
        raise ValueError("battery_metadata must be 'embedded' or 'separate'")

    root = p if p.is_dir() else p.parent
    out_root = Path(out_dir) if out_dir else root
    data_root = out_root / "data" if layout_mode == "nested" else out_root

    def _strip_all_suffixes(path: Path) -> Path:
        name = path.name
        while True:
            suffix = Path(name).suffix
            if not suffix:
                break
            name = Path(name).stem
        return path.with_name(name)

    def _output_path(src: Path) -> Path:
        rel = src.relative_to(root) if src.is_relative_to(root) else Path(src.name)
        base = _strip_all_suffixes(rel)
        suffix = ".bdf.parquet" if fmt == "parquet" else ".bdf.csv"
        return data_root / base.parent / f"{base.name}{suffix}"

    def _metadata_output_path(out_path: Path) -> Path:
        base = _strip_all_suffixes(out_path)
        return base.with_suffix(".jsonld")

    def _parse_filename_parts(path: Path) -> dict[str, str]:
        base = _strip_all_suffixes(path).name
        parts = base.split("__")
        if len(parts) < 5:
            return {}
        institution = parts[0]
        cell_id = parts[1]
        date = parts[2]
        technique = parts[3]
        ambient = "__".join(parts[4:]) if len(parts) > 4 else ""
        return {
            "institution": institution,
            "cell_id": cell_id,
            "date": date,
            "measurement_technique": technique,
            "ambient": ambient,
        }

    def _parse_cell_id(path: Path) -> Optional[str]:
        parts = _parse_filename_parts(path)
        return parts.get("cell_id")

    def _short_cell_id(cell_id: str) -> str:
        return cell_id.rsplit("-", 1)[-1] if "-" in cell_id else cell_id

    def _match_cell_id_from_name(path: Path, keys: list[str]) -> Optional[str]:
        name = _strip_all_suffixes(path).name.lower()
        for key in keys:
            if key and key in name:
                return key
        return None

    # Snapshot file list before writing outputs
    if p.is_dir():
        pattern = "**/*" if recursive else "*"
        files = [f for f in p.glob(pattern) if f.is_file()]
    else:
        files = [p]

    from .io import save as _save  # lazy import

    summary = {
        "converted": [],
        "validated": [],
        "failed": [],
        "skipped": [],
        "metadata": [],
        "metadata_failed": [],
    }

    state_path = root / ".bdf.state.json"
    state: dict[str, Any] = {"version": 1, "items": {}}

    def _load_json(path: Path) -> dict:
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_state() -> None:
        if not incremental or not state_path.exists():
            return
        try:
            raw = _load_json(state_path)
            if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
                state["items"] = raw["items"]
        except Exception:
            state["items"] = {}

    def _save_state() -> None:
        if not incremental:
            return
        import json
        from datetime import datetime, timezone
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _file_signature(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {"mtime": stat.st_mtime, "size": stat.st_size}

    def _state_key(path: Path) -> str:
        try:
            rel = path.relative_to(root)
        except Exception:
            rel = Path(path.name)
        return rel.as_posix()

    def _is_metadata_file(path: Path) -> bool:
        name = path.name.lower()
        if name in {
            "collection.json",
            "dataset.json",
            "battery.json",
            "person.json",
            "people.json",
            "data_download.json",
            "bdf.mapping.json",
            "bdf.map.json",
            "metadata.jsonld",
            "metadata.html",
            ".bdf.state.json",
        }:
            return True
        if name.endswith(".map.json") or name.endswith(".mapping.json"):
            return True
        return name.startswith("metadata.")

    _load_state()

    def _filter_fields(cls, data: dict) -> dict:
        allowed = set(getattr(cls, "__dataclass_fields__", {}).keys())
        return {k: v for k, v in data.items() if k in allowed}

    def _guess_encoding_format(path: Path) -> Optional[str]:
        sfx = "".join(path.suffixes).lower()
        if sfx.endswith(".csv"):
            return "text/csv"
        if sfx.endswith(".tsv"):
            return "text/tab-separated-values"
        if sfx.endswith(".txt"):
            return "text/plain"
        if sfx.endswith(".json"):
            return "application/json"
        if sfx.endswith(".parquet"):
            return "application/x-parquet"
        if sfx.endswith(".zip"):
            return "application/zip"
        if sfx.endswith(".nda") or sfx.endswith(".ndax"):
            return "application/octet-stream"
        return None

    def _load_people_index(dir_path: Path) -> dict[str, dict]:
        for name in ("person.json", "people.json"):
            people_path = dir_path / name
            if not people_path.exists():
                continue
            people_raw = _load_json(people_path)
            people_index: dict[str, dict] = {}
            if isinstance(people_raw, dict):
                for pid, pdata in people_raw.items():
                    if isinstance(pdata, dict):
                        people_index[str(pid).lower()] = pdata
            elif isinstance(people_raw, list):
                for pdata in people_raw:
                    if isinstance(pdata, dict) and pdata.get("id") is not None:
                        people_index[str(pdata["id"]).lower()] = pdata
            return people_index
        return {}

    def _expand_battery_items(battery_raw: Any) -> list[dict]:
        if isinstance(battery_raw, list):
            return [item for item in battery_raw if isinstance(item, dict)]
        if isinstance(battery_raw, dict):
            if "ids" in battery_raw and isinstance(battery_raw.get("ids"), list):
                spec = battery_raw.get("spec")
                if not isinstance(spec, dict):
                    spec = {k: v for k, v in battery_raw.items() if k != "ids"}
                manufacturer = spec.get("manufacturer")
                model = spec.get("model")
                batch = spec.get("batch")
                namespace = spec.get("namespace")
                name_template = spec.get("name_template")
                id_template = spec.get("id_template")
                iri_template = spec.get("iri_template")
                use_short_id = bool(name_template)

                def _format_template(template: str, *, short_id: str, full_id: str, name: Optional[str]) -> str:
                    return str(template).format(
                        manufacturer=manufacturer,
                        model=model,
                        batch=batch,
                        namespace=namespace,
                        id=short_id,
                        short_id=short_id,
                        full_id=full_id,
                        name=name or full_id,
                    )

                def _build_full_id(short_id: str) -> str:
                    if id_template:
                        return _format_template(
                            id_template,
                            short_id=short_id,
                            full_id=short_id,
                            name=None,
                        )
                    if manufacturer and model and batch:
                        return f"{manufacturer}-{model}-{batch}-{short_id}"
                    return short_id

                def _build_name(short_id: str, full_id: str) -> Optional[str]:
                    if name_template:
                        return _format_template(
                            name_template,
                            short_id=short_id,
                            full_id=full_id,
                            name=None,
                        ).lower()
                    return None

                def _build_id(short_id: str, full_id: str) -> str:
                    return short_id if use_short_id else full_id

                def _build_iri(short_id: str, full_id: str, name: Optional[str]) -> Optional[str]:
                    if iri_template:
                        return _format_template(
                            iri_template,
                            short_id=short_id,
                            full_id=full_id,
                            name=name,
                        ).lower()
                    if namespace:
                        base = str(namespace).rstrip("/")
                        if manufacturer and model and batch:
                            return f"{base}/{manufacturer}/{model}/{batch}/{short_id}".lower()
                        return f"{base}/{short_id}".lower()
                    return None

                items: list[dict] = []
                for entry in battery_raw.get("ids", []):
                    if entry is None:
                        continue
                    if isinstance(entry, dict):
                        short_id = entry.get("short_id") or entry.get("id")
                        if short_id is None:
                            continue
                        short_id = str(short_id)
                        full_id = str(entry.get("full_id") or _build_full_id(short_id))
                        name = entry.get("name") or _build_name(short_id, full_id)
                        if name:
                            name = str(name).lower()
                        iri = entry.get("iri") or _build_iri(short_id, full_id, name)
                        if iri:
                            iri = str(iri).lower()
                        item = {**spec, **entry}
                        item["id"] = _build_id(short_id, full_id)
                        if name:
                            item["name"] = name
                        if iri:
                            item["iri"] = iri
                        items.append(item)
                        continue
                    short_id = str(entry)
                    full_id = _build_full_id(short_id)
                    name = _build_name(short_id, full_id)
                    if name:
                        name = str(name).lower()
                    iri = _build_iri(short_id, full_id, name)
                    if iri:
                        iri = str(iri).lower()
                    item = {**spec, "id": _build_id(short_id, full_id)}
                    if name:
                        item["name"] = name
                    if iri:
                        item["iri"] = iri
                    items.append(item)
                return items
            return [battery_raw]
        return []

    def _build_battery_index(dir_path: Path) -> dict[str, Any]:
        from .metadata import Battery  # lazy import

        battery_path = dir_path / "battery.json"
        if not battery_path.exists():
            return {}
        battery_raw = _load_json(battery_path)
        battery_items = _expand_battery_items(battery_raw)
        batteries = [
            Battery(**_filter_fields(Battery, item))
            for item in battery_items
            if isinstance(item, dict)
        ]
        index: dict[str, Battery] = {}
        for b in batteries:
            if b.id:
                index[str(b.id).lower()] = b
            if b.name:
                index.setdefault(str(b.name).lower(), b)
        return index

    def _resolve_creator(item: Any, people_index: dict[str, dict]):
        from .metadata import Creator  # lazy import
        if isinstance(item, str):
            pdata = people_index.get(item.lower())
            if not pdata:
                raise ValueError(f"Creator id not found in person.json: {item}")
            return Creator(**_filter_fields(Creator, pdata))
        if isinstance(item, dict):
            if "id" in item and (len(item) == 1 or all(k in {"id"} for k in item)):
                pid = str(item["id"]).lower()
                pdata = people_index.get(pid)
                if not pdata:
                    raise ValueError(f"Creator id not found in person.json: {item['id']}")
                return Creator(**_filter_fields(Creator, pdata))
            return Creator(**_filter_fields(Creator, item))
        return None

    def _build_creators(meta_raw: dict, people_index: dict[str, dict]):
        creators_raw = meta_raw.get("creators") or meta_raw.get("creator") or []
        creators = [c for c in (_resolve_creator(it, people_index) for it in creators_raw) if c is not None]
        if not creators and people_index:
            from .metadata import Creator  # lazy import
            creators = [Creator(**_filter_fields(Creator, pdata)) for pdata in people_index.values()]
        return creators

    def _write_metadata(src: Path, *, df: pd.DataFrame, out_path: Path) -> Optional[Path]:
        dataset_path = src.parent / "dataset.json"
        if not dataset_path.exists():
            return None

        from .metadata import Dataset, DataDownload, Battery  # lazy import

        meta_raw = _load_json(dataset_path)
        url_base = meta_raw.get("url_base")
        people_index = _load_people_index(src.parent)
        creators = _build_creators(meta_raw, people_index)
        if not creators:
            raise ValueError("dataset.json must include at least one creator entry (or person.json).")

        title = meta_raw.get("title")
        description = meta_raw.get("description")
        if not title or not description:
            raise ValueError("dataset.json must include 'title' and 'description'.")

        meta_kwargs = dict(meta_raw)
        meta_kwargs.pop("url_base", None)
        meta_kwargs.pop("creators", None)
        meta_kwargs.pop("creator", None)
        meta_kwargs["creators"] = creators
        meta = Dataset(**meta_kwargs)

        rel_path = src.relative_to(src.parent) if src.is_relative_to(src.parent) else Path(src.name)
        base_url = f"{url_base.rstrip('/')}/{rel_path.as_posix().lstrip('/')}" if url_base else src.name
        base_name = src.name
        base_encoding = _guess_encoding_format(src)

        download_path = src.parent / "data_download.json"
        dists: list[DataDownload] = []
        if download_path.exists():
            dd_raw = _load_json(download_path)
            dd_list = dd_raw if isinstance(dd_raw, list) else [dd_raw]
            for item in dd_list:
                if not isinstance(item, dict):
                    continue
                dd_item = {
                    "url": base_url,
                    "name": base_name,
                    "encoding_format": base_encoding,
                }
                if item.get("path"):
                    path = str(item["path"]).lstrip("/")
                    dd_item["url"] = f"{url_base.rstrip('/')}/{path}" if url_base else path
                    if not item.get("name"):
                        dd_item["name"] = Path(path).name
                if item.get("url"):
                    dd_item["url"] = item["url"]
                for key, value in item.items():
                    if key in {"url", "path"}:
                        continue
                    dd_item[key] = value
                dists.append(DataDownload(**_filter_fields(DataDownload, dd_item)))
        if not dists:
            dists = [DataDownload(url=base_url, name=base_name, encoding_format=base_encoding)]

        battery_path = src.parent / "battery.json"
        if not battery_path.exists():
            raise FileNotFoundError(
                f"{battery_path} not found (required when dataset.json is present)."
            )
        battery_raw = _load_json(battery_path)
        battery_items = _expand_battery_items(battery_raw)
        batteries = [
            Battery(**_filter_fields(Battery, item))
            for item in battery_items
            if isinstance(item, dict)
        ]
        if not batteries:
            raise ValueError(f"{battery_path} must contain a battery object or a list of objects.")

        cell_id = _parse_cell_id(src)
        if not cell_id:
            key_list = []
            for b in batteries:
                if b.id:
                    key_list.append(str(b.id).lower())
                if b.name:
                    key_list.append(str(b.name).lower())
            cell_id = _match_cell_id_from_name(src, key_list)

        if cell_id:
            cell_id_lower = cell_id.lower()
            matched = [
                b
                for b in batteries
                if str(b.id).lower() == cell_id_lower
                or (b.name and str(b.name).lower() == cell_id_lower)
            ]
        else:
            matched = []
        if matched:
            batteries = matched

        about_value = [b.to_schemaorg() for b in batteries]
        if len(about_value) == 1:
            about_value = about_value[0]

        extra_fields = {"schema:about": about_value}
        meta_out = _metadata_output_path(out_path)
        meta.save_jsonld(meta_out, distributions=dists, extra_fields=extra_fields, df=df)
        return meta_out

    def _parse_measurement_technique(path: Path) -> Optional[str]:
        parts = _parse_filename_parts(path)
        return parts.get("measurement_technique")

    def _write_collection_metadata(
        *, include_batteries: bool = False
    ) -> tuple[Optional[Path], dict[str, list[str]]]:
        dataset_path = root / "collection.json"
        if not dataset_path.exists():
            return None, {}

        from .metadata import Dataset, DataDownload  # lazy import

        meta_raw = _load_json(dataset_path)
        url_base = meta_raw.get("url_base")
        collection_doi = meta_raw.get("doi")
        people_index = _load_people_index(root)
        creators = _build_creators(meta_raw, people_index)
        if not creators:
            raise ValueError("collection.json must include at least one creator entry (or person.json).")

        title = meta_raw.get("title")
        description = meta_raw.get("description")
        if not title or not description:
            raise ValueError("collection.json must include 'title' and 'description'.")

        meta_kwargs = dict(meta_raw)
        meta_kwargs.pop("url_base", None)
        meta_kwargs.pop("creators", None)
        meta_kwargs.pop("creator", None)
        meta_kwargs["creators"] = creators
        meta = Dataset(**meta_kwargs)

        def _is_bdf_output(path: Path) -> bool:
            sfx = "".join(path.suffixes).lower()
            return ".bdf" in sfx

        bdf_files = [
            f
            for f in data_root.rglob("*")
            if f.is_file() and _is_bdf_output(f)
        ]
        battery_index = _build_battery_index(root)
        child_nodes: list[dict[str, Any]] = []
        dataset_links: dict[str, list[str]] = {}
        for f in sorted(bdf_files):
            try:
                rel = f.relative_to(out_root)
            except Exception:
                try:
                    rel = f.relative_to(root)
                except Exception:
                    rel = Path(f.name)
            rel_posix = rel.as_posix().lstrip("/")
            url = f"{url_base.rstrip('/')}/{rel_posix}" if url_base else rel_posix
            encoding = _guess_encoding_format(f)
            dist = DataDownload(url=url, name=f.name, encoding_format=encoding)

            technique = _parse_measurement_technique(f)
            child_title = f"{meta.title} - {technique}" if technique else f"{meta.title} - {f.name}"
            child_desc = meta.description
            if technique and technique.lower() not in (meta.description or "").lower():
                child_desc = f"{meta.description} Measurement technique: {technique}."

            child_kwargs: dict[str, Any] = {
                "title": child_title,
                "creators": creators,
                "description": child_desc,
                "keywords": meta.keywords,
                "license": meta.license,
                "version": meta.version,
                "publication_date": meta.publication_date,
                "measurement_technique": technique,
                "citation": meta.citation,
            }

            override_path = root / rel.parent / "dataset.json"
            if not override_path.exists():
                override_path = root / "dataset.json"
            child_identifier = rel_posix
            if override_path.exists():
                override_raw = _load_json(override_path)
                if isinstance(override_raw, dict):
                    override_creators = _build_creators(override_raw, people_index)
                    if override_creators:
                        child_kwargs["creators"] = override_creators
                    override_raw = dict(override_raw)
                    override_raw.pop("creators", None)
                    override_raw.pop("creator", None)
                    override_raw.pop("url_base", None)
                    if "measurementTechnique" in override_raw and "measurement_technique" not in override_raw:
                        override_raw["measurement_technique"] = override_raw.pop("measurementTechnique")
                    if override_raw.get("doi"):
                        child_kwargs["doi"] = override_raw["doi"]
                    override_filtered = _filter_fields(Dataset, override_raw)
                    for key, value in override_filtered.items():
                        if value is not None:
                            child_kwargs[key] = value
                    if override_raw.get("identifier"):
                        child_identifier = override_raw["identifier"]

            if collection_doi and not child_kwargs.get("doi"):
                child_kwargs["doi"] = collection_doi

            dataset_uri = None
            if url:
                dataset_uri = f"{url}#dataset"
            elif child_identifier:
                dataset_uri = f"bdf:dataset/{child_identifier}"

            child_meta = Dataset(**child_kwargs)
            extra_fields: dict[str, Any] = {}
            cell_id = _parse_cell_id(f)
            if not cell_id and battery_index:
                cell_id = _match_cell_id_from_name(f, list(battery_index.keys()))
            if cell_id and battery_index:
                battery = battery_index.get(cell_id.lower())
                if battery:
                    extra_fields["schema:about"] = {"@id": battery.to_schemaorg().get("@id")}
                    if dataset_uri:
                        dataset_links.setdefault(cell_id.lower(), []).append(dataset_uri)
            child_obj = child_meta.to_schemaorg_dataset(
                dataset_uri=dataset_uri,
                identifier=child_identifier,
                distributions=[dist],
                context=[],
                extra_fields=extra_fields or None,
            )
            child_obj.pop("@context", None)
            child_nodes.append(child_obj)

        extra_fields = {"schema:hasPart": child_nodes} if child_nodes else {}
        meta_out = out_root / "metadata.jsonld"

        if include_batteries and battery_index:
            from .metadata import DEFAULT_JSONLD_CONTEXT  # lazy import
            import json

            dataset_obj = meta.to_schemaorg_dataset(
                extra_fields=extra_fields or None,
                context=[],
            )
            dataset_obj.pop("@context", None)

            batteries: list[Any] = []
            seen_ids: set[str] = set()
            for battery in battery_index.values():
                if not battery.id:
                    continue
                key = str(battery.id).lower()
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                batteries.append(battery)

            battery_nodes: list[dict[str, Any]] = []
            for battery in batteries:
                battery_doc = battery.to_schemaorg()
                key = None
                if battery.name and battery.name.lower() in dataset_links:
                    key = battery.name.lower()
                elif battery.id and battery.id.lower() in dataset_links:
                    key = battery.id.lower()
                if key:
                    dataset_refs = [{"@id": uri} for uri in dataset_links.get(key, [])]
                    if dataset_refs:
                        battery_doc["schema:subjectOf"] = dataset_refs
                battery_nodes.append(battery_doc)

            graph_obj = {"@context": list(DEFAULT_JSONLD_CONTEXT), "@graph": [dataset_obj, *battery_nodes]}
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(graph_obj, f, ensure_ascii=False, indent=2)
        else:
            meta.save_jsonld(meta_out, extra_fields=extra_fields or None)
        return meta_out, dataset_links

    def _write_battery_metadata_files(
        battery_index: dict[str, Any], dataset_links: dict[str, list[str]]
    ) -> list[Path]:
        from .metadata import DEFAULT_JSONLD_CONTEXT  # lazy import
        import json

        meta_paths: list[Path] = []
        batteries: list[Any] = []
        seen_ids: set[str] = set()
        for battery in battery_index.values():
            if not battery.id:
                continue
            key = str(battery.id).lower()
            if key in seen_ids:
                continue
            seen_ids.add(key)
            batteries.append(battery)

        for battery in batteries:
            meta_out = out_root / f"{battery.id}.metadata.jsonld"
            battery_doc = {"@context": list(DEFAULT_JSONLD_CONTEXT), **battery.to_schemaorg()}
            dataset_refs: list[dict[str, str]] = []
            key = None
            if battery.name and battery.name.lower() in dataset_links:
                key = battery.name.lower()
            elif battery.id and battery.id.lower() in dataset_links:
                key = battery.id.lower()
            if key:
                dataset_refs = [{"@id": uri} for uri in dataset_links.get(key, [])]
            if dataset_refs:
                battery_doc["schema:subjectOf"] = dataset_refs
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(battery_doc, f, ensure_ascii=False, indent=2)
            meta_paths.append(meta_out)
        return meta_paths

    def _write_nested_metadata() -> list[Path]:
        dataset_path = root / "collection.json"
        if not dataset_path.exists():
            raise FileNotFoundError("collection.json is required for nested metadata generation.")

        from .metadata import DEFAULT_JSONLD_CONTEXT  # lazy import
        import json

        battery_index = _build_battery_index(root)
        if not battery_index:
            raise ValueError("battery.json is required for nested metadata generation.")

        meta_paths: list[Path] = []
        root_meta, dataset_links = _write_collection_metadata()
        if root_meta:
            meta_paths.append(root_meta)

        batteries: list[Any] = []
        seen_ids: set[str] = set()
        for battery in battery_index.values():
            if not battery.id:
                continue
            key = str(battery.id).lower()
            if key in seen_ids:
                continue
            seen_ids.add(key)
            batteries.append(battery)

        for battery in batteries:
            cell_id = str(battery.id)
            cell_dir = out_root / cell_id
            cell_dir.mkdir(parents=True, exist_ok=True)
            meta_out = cell_dir / "metadata.jsonld"
            battery_doc = {"@context": list(DEFAULT_JSONLD_CONTEXT), **battery.to_schemaorg()}
            dataset_refs: list[dict[str, str]] = []
            if dataset_links:
                key = None
                if battery.name and battery.name.lower() in dataset_links:
                    key = battery.name.lower()
                elif battery.id and battery.id.lower() in dataset_links:
                    key = battery.id.lower()
                if key:
                    dataset_refs = [{"@id": uri} for uri in dataset_links.get(key, [])]
            if dataset_refs:
                battery_doc["schema:subjectOf"] = dataset_refs
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(battery_doc, f, ensure_ascii=False, indent=2)
            meta_paths.append(meta_out)

        return meta_paths

    collection_metadata = layout_mode == "flat" and p.is_dir() and (root / "collection.json").exists()

    for f in files:
        try:
            if _is_metadata_file(f):
                summary["skipped"].append({"path": str(f), "reason": "metadata_file"})
                continue

            if _looks_like_bdf_artifact(f):
                output_used = f
                out_path = _output_path(f)

                def _place_existing(src: Path, dst: Path) -> Path:
                    if dst.resolve() == src.resolve():
                        return src
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        if force:
                            dst.unlink()
                            shutil.move(src, dst)
                            return dst
                        summary["skipped"].append({"path": str(src), "reason": "output_exists"})
                        return dst
                    shutil.move(src, dst)
                    return dst

                if layout_mode == "nested":
                    if not f.is_relative_to(data_root):
                        output_used = _place_existing(f, out_path)
                else:
                    output_used = _place_existing(f, out_path)

                if validate_existing:
                    rep = validate(output_used, report=False, raise_on_error=False)
                    summary["validated"].append({"path": str(output_used), "ok": rep.get("ok"), "report": rep})

                existing_entry = {"path": str(f), "output": str(output_used), "existing_bdf": True}
                if layout_mode == "flat" and not collection_metadata:
                    df_for_meta = None
                    try:
                        from .io import load as _load_bdf  # lazy import
                        df_for_meta = _load_bdf(output_used)
                    except Exception:
                        df_for_meta = None
                    try:
                        meta_path = _write_metadata(output_used, df=df_for_meta, out_path=output_used)
                        if meta_path:
                            existing_entry["metadata"] = str(meta_path)
                            summary["metadata"].append({"path": str(output_used), "metadata": str(meta_path)})
                    except Exception as meta_err:
                        summary["metadata_failed"].append({"path": str(output_used), "error": str(meta_err)})
                        if raise_on_error:
                            raise
                summary["converted"].append(existing_entry)
                continue
            if incremental and not force:
                key = _state_key(f)
                current = _file_signature(f)
                prev = state["items"].get(key)
                if prev and prev.get("mtime") == current["mtime"] and prev.get("size") == current["size"]:
                    summary["skipped"].append({"path": str(f), "reason": "unchanged"})
                    continue
                if prev and (prev.get("mtime") != current["mtime"] or prev.get("size") != current["size"]):
                    output_ref = prev.get("output")
                    output_path = None
                    if output_ref:
                        output_path = (root / output_ref).resolve()
                    if output_path and output_path.exists():
                        summary["skipped"].append({"path": str(f), "reason": "changed"})
                        continue

            df = read(
                f,
                plugin=plugin,
                validate=validate_converted,
                include_optional=include_optional,
            )
            out_path = _output_path(f)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _save(df, out_path, index=False)
            converted_entry = {"path": str(f), "output": str(out_path)}
            if incremental:
                key = _state_key(f)
                sig = _file_signature(f)
                output_rel = None
                try:
                    output_rel = out_path.relative_to(root).as_posix()
                except Exception:
                    output_rel = str(out_path)
                state["items"][key] = {**sig, "output": output_rel}
            if layout_mode == "flat" and not collection_metadata:
                try:
                    meta_path = _write_metadata(f, df=df, out_path=out_path)
                    if meta_path:
                        converted_entry["metadata"] = str(meta_path)
                        summary["metadata"].append({"path": str(f), "metadata": str(meta_path)})
                except Exception as meta_err:
                    summary["metadata_failed"].append({"path": str(f), "error": str(meta_err)})
                    if raise_on_error:
                        raise
            summary["converted"].append(converted_entry)
        except Exception as e:
            summary["failed"].append({"path": str(f), "error": str(e)})
            if raise_on_error:
                raise

    if collection_metadata:
        try:
            include_batteries = battery_mode == "embedded"
            meta_path, dataset_links = _write_collection_metadata(include_batteries=include_batteries)
            if meta_path:
                summary["metadata"].append({"path": str(root), "metadata": str(meta_path)})
            if battery_mode == "separate":
                battery_index = _build_battery_index(root)
                if battery_index:
                    for meta_path in _write_battery_metadata_files(battery_index, dataset_links):
                        summary["metadata"].append({"path": str(meta_path.parent), "metadata": str(meta_path)})
        except Exception as meta_err:
            summary["metadata_failed"].append({"path": str(root), "error": str(meta_err)})
            if raise_on_error:
                raise
    elif layout_mode == "nested" and p.is_dir():
        try:
            meta_paths = _write_nested_metadata()
            for meta_path in meta_paths:
                summary["metadata"].append({"path": str(meta_path.parent), "metadata": str(meta_path)})
        except Exception as meta_err:
            summary["metadata_failed"].append({"path": str(root), "error": str(meta_err)})
            if raise_on_error:
                raise

    _save_state()

    return summary
