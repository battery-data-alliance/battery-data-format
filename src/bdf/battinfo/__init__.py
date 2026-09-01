"""BattINFO record models.

The record models are generated from the bundled upstream schemas
(:mod:`bdf.battinfo.generated`).
"""

from __future__ import annotations


def bundled_ref() -> str | None:
    """Return the upstream BattINFO commit the bundled schemas were fetched at.

    Reads the ``ref`` line of ``bdf/data/battinfo/VERSION``. Upstream publishes
    no schema version numbers yet, so the ref is a commit hash; when BattINFO
    starts cutting versioned schema releases, this becomes a release tag.

    Returns:
        The ref string, or None when the stamp is missing or unreadable.
    """
    import importlib.resources

    try:
        text = importlib.resources.files("bdf.data").joinpath("battinfo", "VERSION").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("ref="):
            return line.removeprefix("ref=").strip() or None
    return None
