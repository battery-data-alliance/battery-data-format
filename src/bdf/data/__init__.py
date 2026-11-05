"""Package data, with one directory for each kind of bundled definition data."""

from importlib.resources import files
from pathlib import Path

ONTOLOGY_BUNDLE = Path(str(files(__name__) / "bdf-ontology"))
"""The bundled BDF ontology: a version directory with ``VERSION`` and ``bdf-ontology.ttl``."""

BATTINFO_BUNDLE = Path(str(files(__name__) / "battinfo"))
"""The bundled BattINFO schema: a version directory with ``VERSION`` and ``schemas/``."""
