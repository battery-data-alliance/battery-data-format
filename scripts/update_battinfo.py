#!/usr/bin/env python
"""Refresh the bundled BattINFO schemas and regenerate the model package.

Usage: ``python scripts/update_battinfo.py REF``
       ``python scripts/update_battinfo.py --check [REF]``

``REF`` is a commit, tag, or branch of ``BIG-MAP/BattINFO``. The script fetches
the managed schema files at that ref, replaces the bundled copies
atomically, stamps ``VERSION`` with the commit the ref resolves to, and
regenerates ``bdf.battinfo.generated``. It writes each file in the canonical
form ``normalize`` states, and not in the upstream byte layout, so an upstream
commit that only reorders keys produces no diff.

``--check`` compares alone, and it exits non-zero when the bundle is stale. It
fetches the managed files at ``REF`` (``main`` by default) and reports which ones
differ from the bundle. The comparison reads parsed content, so an upstream
commit that reformats a file, or that touches nothing under ``assets/schemas/``,
reports no difference. CI runs this mode on every pull request, which is why an
unrelated upstream commit does not turn a pull request red.

BDF pins nine upstream schema files under ``bdf/data/battinfo/schemas/``: the
five entity schemas (``test``, ``cell-instance``, ``channel``, ``equipment``,
``test-protocol``), ``cell-instance``'s ``cell-canonical`` dependency, and the
two shared ``modules/common`` files ``channel`` and ``equipment`` depend on.
``bdf/data/battinfo/VERSION`` stamps the ref they came from.

The regeneration is ``datamodel-codegen`` with no arguments: the whole render
is declared under ``[tool.datamodel-codegen]`` in ``pyproject.toml``, and CI
verifies the committed package with ``datamodel-codegen --check``.

Nothing in ``bdf`` imports this module. The schema files ship as package data
for a reader that wants them, and the loaders here serve the contract tests.
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO = "BIG-MAP/BattINFO"
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Upstream paths (within BIG-MAP/BattINFO) for the managed schema files,
# keyed by their path relative to the bundled schema directory.
MANAGED_SCHEMA_PATHS: dict[str, str] = {
    "test.schema.json": "assets/schemas/test.schema.json",
    "cell-instance.schema.json": "assets/schemas/cell-instance.schema.json",
    "cell-canonical.schema.json": "assets/schemas/cell-canonical.schema.json",
    "channel.schema.json": "assets/schemas/channel.schema.json",
    "equipment.schema.json": "assets/schemas/equipment.schema.json",
    "test-protocol.schema.json": "assets/schemas/test-protocol.schema.json",
    "dataset.schema.json": "assets/schemas/dataset.schema.json",
    "modules/common/quantitative-properties.schema.json": "assets/schemas/modules/common/quantitative-properties.schema.json",
    "modules/common/quantity.schema.json": "assets/schemas/modules/common/quantity.schema.json",
}

RAW_URL_TEMPLATE = f"https://raw.githubusercontent.com/{_REPO}/{{ref}}/{{path}}"
"""The raw upstream URL one managed file is fetched from, by ref and path."""

COMMIT_URL_TEMPLATE = f"https://api.github.com/repos/{_REPO}/commits/{{ref}}"
"""The upstream API URL that resolves a branch, tag, or commit to a commit."""

DEFAULT_BRANCH = "main"
"""The upstream branch a check reads when the caller names no ref."""

_REFRESH_HINT = "Run scripts/update_battinfo.py REF to refresh the bundle."


def bundle_dir() -> Path:
    """Return the bundled package-data directory for the BattINFO schemas.

    Returns:
        The directory holding the ``VERSION`` stamp and the ``schemas``
        directory.
    """
    return Path(str(importlib.resources.files("bdf.data").joinpath("battinfo")))


def schema_dir() -> Path:
    """Return the directory holding the managed schema files.

    Returns:
        The generator's input directory, which holds schema files alone.
    """
    return bundle_dir() / "schemas"


def load_managed(source: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the managed schema files the model package is rendered from.

    Args:
        source: Directory holding the schema files. Defaults to the bundled
            :func:`schema_dir`.

    Returns:
        A mapping from each managed file's path relative to ``source`` (e.g.
        ``"modules/common/quantity.schema.json"``) to its parsed content.

    Raises:
        RuntimeError: The directory or a managed schema file is missing or
            unparseable.
    """
    if source is None:
        source = schema_dir()
    try:
        return {
            rel_name: json.loads((source / rel_name).read_text(encoding="utf-8")) for rel_name in MANAGED_SCHEMA_PATHS
        }
    except Exception as exc:
        raise RuntimeError(
            f"Bundled BattINFO schemas at {source} missing or unreadable: {exc}. {_REFRESH_HINT}"
        ) from exc


def load_schema(name: str, source: Path | None = None) -> dict[str, Any]:
    """Load one managed schema file by its bundle-relative name.

    Args:
        name: A key of :data:`MANAGED_SCHEMA_PATHS`, e.g. ``"test.schema.json"``.
        source: Directory holding the schema files. Defaults to the bundled
            :func:`schema_dir`.

    Returns:
        The parsed schema document.

    Raises:
        KeyError: ``name`` is not a managed schema file.
        RuntimeError: The file is missing or unparseable.
    """
    if name not in MANAGED_SCHEMA_PATHS:
        raise KeyError(f"{name!r} is not a managed BattINFO schema file")
    if source is None:
        source = schema_dir()
    try:
        return json.loads((source / name).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Bundled BattINFO schema {source / name} missing or unreadable: {exc}. {_REFRESH_HINT}"
        ) from exc


def load_version(source: Path | None = None) -> dict[str, str]:
    """Parse the ``key=value`` per line ``VERSION`` stamp beside the schemas.

    Args:
        source: Directory holding the ``VERSION`` file. Defaults to the
            bundled :func:`bundle_dir`.

    Returns:
        A mapping of stamp keys (``repo``, ``ref``) to their values.

    Raises:
        RuntimeError: The stamp is missing or unreadable.
    """
    if source is None:
        source = bundle_dir()
    try:
        lines = (source / "VERSION").read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        raise RuntimeError(
            f"Bundled BattINFO VERSION stamp at {source} missing or unreadable: {exc}. {_REFRESH_HINT}"
        ) from exc
    return dict(line.split("=", 1) for line in lines if "=" in line)


def fetch_schemas(ref: str) -> dict[str, bytes]:
    """Fetch the managed schema files at ``ref``.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` commit or tag to pin.

    Returns:
        A mapping from each file's bundle-relative path to its raw bytes.

    Raises:
        requests.HTTPError: A schema fetch failed.
        RuntimeError: Fetched content is not valid JSON.
    """
    import requests

    fetched: dict[str, bytes] = {}
    for rel_name, upstream_path in MANAGED_SCHEMA_PATHS.items():
        url = RAW_URL_TEMPLATE.format(ref=ref, path=upstream_path)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        content = response.content
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Fetched content for {rel_name} from {url} is not valid JSON: {exc}") from exc
        fetched[rel_name] = content
    return fetched


def resolve_ref(ref: str) -> str:
    """Resolve a branch, tag, or commit to the commit it names.

    The stamp records a commit and never a branch, because a branch moves. A
    test that refetches at a branch compares against content that upstream can
    change, and a stamp that pins a commit stays true.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` branch, tag, or commit.

    Returns:
        The full commit SHA the ref names.

    Raises:
        requests.HTTPError: The resolution request failed.
        RuntimeError: The response carries no commit SHA.
    """
    import requests

    url = COMMIT_URL_TEMPLATE.format(ref=ref)
    response = requests.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    sha = response.json().get("sha")
    if not isinstance(sha, str) or not sha:
        raise RuntimeError(f"Response from {url} carries no commit SHA for ref {ref!r}")
    return sha


def compare(ref: str, source: Path | None = None) -> dict[str, bytes]:
    """Report the managed schema files that differ from the bundle at ``ref``.

    The comparison reads parsed content, not raw bytes. An upstream commit that
    only reformats a file therefore reports no difference, and neither does a
    commit that leaves ``assets/schemas/`` alone.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` branch, tag, or commit to fetch.
        source: Directory holding the bundled schema files. Defaults to the
            bundled :func:`schema_dir`.

    Returns:
        A mapping from each differing file's bundle-relative path to the bytes
        fetched at ``ref``. An empty mapping means the bundle is current.

    Raises:
        requests.HTTPError: A schema fetch failed.
        RuntimeError: Fetched content is not valid JSON, or the bundle is
            missing or unreadable.
    """
    bundled = load_managed(source)
    fetched = fetch_schemas(ref)
    return {rel_name: content for rel_name, content in fetched.items() if json.loads(content) != bundled[rel_name]}


def normalize(content: bytes) -> bytes:
    """Re-serialize JSON bytes into the repository's canonical form.

    The bundle stores every schema file with sorted keys and a two-space
    indent, so an upstream commit that only reorders keys produces no diff.
    The form matches the ``pretty-format-json`` pre-commit hook, which runs
    over these files with its default ``--indent 2`` and its default key sort.
    A bundled file therefore stays stable across a commit.

    Args:
        content: Raw JSON bytes, as fetched from upstream.

    Returns:
        The same document, serialized with sorted keys, a two-space indent,
        escaped non-ASCII characters, and a trailing newline.

    Raises:
        json.JSONDecodeError: ``content`` is not valid JSON.
    """
    text = json.dumps(json.loads(content), indent=2, sort_keys=True, ensure_ascii=True)
    return f"{text}\n".encode()


def _atomic_write(dest_dir: Path, rel_name: str, content: bytes) -> None:
    """Write ``content`` to ``dest_dir / rel_name``, replacing it atomically.

    Args:
        dest_dir: Directory to write into.
        rel_name: File name, possibly nested (e.g. ``modules/common/x.json``),
            relative to ``dest_dir``. Parent directories are created as needed.
        content: Raw bytes to write.
    """
    dest = dest_dir / rel_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False, suffix=".tmp") as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(content)
    tmp_path.replace(dest)


def write_bundle(fetched: dict[str, bytes], ref: str, dest: Path | None = None) -> Path:
    """Replace the bundled schema files and stamp ``VERSION`` with ``ref``.

    Each schema file passes through :func:`normalize` first, so the bundle
    holds the upstream document and never the upstream byte layout.

    Args:
        fetched: The mapping :func:`fetch_schemas` returned.
        ref: The upstream ref the files came from.
        dest: Bundle directory to write into. Defaults to the bundled
            package-data directory.

    Returns:
        The bundle directory written to.
    """
    if dest is None:
        dest = bundle_dir()
    schemas = dest / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    for rel_name, content in fetched.items():
        _atomic_write(schemas, rel_name, normalize(content))
    _atomic_write(dest, "VERSION", f"repo=BIG-MAP/BattINFO\nref={ref}\n".encode())
    return dest


def regenerate() -> None:
    """Regenerate ``bdf.battinfo.generated`` from the bundled schema files.

    Runs in the repository root, because the generator reads
    ``[tool.datamodel-codegen]`` from the working directory and that section
    states the input and output paths relative to it.

    Raises:
        subprocess.CalledProcessError: The generator exited non-zero.
    """
    subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def update(ref: str, dest: Path | None = None) -> Path:
    """Fetch, stamp, and regenerate in one run.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` commit to pin. The caller resolves a
            branch or a tag first, because the stamp records a commit.
        dest: Bundle directory to write into. Defaults to the bundled
            package-data directory. The regeneration always reads the
            directory the generator configuration names, whatever ``dest``
            states.

    Returns:
        The bundle directory written to.
    """
    written = write_bundle(fetch_schemas(ref), ref, dest)
    regenerate()
    return written


def main(argv: list[str] | None = None) -> None:
    """Entry point: check the bundle against a ref, or refresh it at that ref.

    ``--check`` prints ``key=value`` per line, so the report names the files
    that moved:

    .. code-block:: text

        changed=true
        ref=<commit the ref resolved to>
        files=<comma-separated bundle-relative names, empty when none differ>

    Args:
        argv: Command-line arguments. Defaults to ``sys.argv[1:]``.

    Raises:
        SystemExit: ``--check`` found a managed schema file that differs from
            the bundle. CI reads that exit code, matching ``--check`` on
            ``datamodel-codegen`` and on ``scripts/generate_docs.py``.
    """
    parser = argparse.ArgumentParser(description="Refresh or check the bundled BattINFO schemas.")
    parser.add_argument(
        "ref",
        nargs="?",
        help=f"Upstream {_REPO} branch, tag, or commit. Defaults to {DEFAULT_BRANCH} under --check.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report which managed schema files differ from the bundle, and write nothing.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.check:
        resolved = resolve_ref(args.ref or DEFAULT_BRANCH)
        differing = compare(resolved)
        print(f"changed={'true' if differing else 'false'}")
        print(f"ref={resolved}")
        print(f"files={','.join(sorted(differing))}")
        if differing:
            raise SystemExit(
                f"{len(differing)} bundled BattINFO schema file(s) differ from {_REPO} at {resolved}. "
                f"Run scripts/update_battinfo.py {resolved} to refresh the bundle."
            )
        return

    requested: str | None = args.ref
    if requested is None:
        parser.error("a ref is required unless --check is given")
    resolved = resolve_ref(requested)
    update(resolved)
    print(f"BattINFO schema bundle updated: {schema_dir()} (ref={resolved})")


if __name__ == "__main__":
    main()
