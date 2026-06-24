import os
import sys
from datetime import datetime

# Ensure src/ is on path for autodoc
sys.path.insert(0, os.path.abspath("../src"))
# Ensure docs/_ext (local Sphinx extensions) is importable
sys.path.insert(0, os.path.abspath("_ext"))

# plotly's default Jupyter mimebundle (application/vnd.plotly.v1+json) has no
# renderer in myst-nb, so plots silently vanish. Force the "notebook_connected"
# renderer, which embeds figures as plain text/html that myst-nb can render.
# Must be set before notebooks execute, since plotly reads this env var at
# import time.
os.environ.setdefault("PLOTLY_RENDERER", "notebook_connected")

project = "Battery Data Format (bdf)"
author = "Battery Data Alliance"
year = datetime.now().year
copyright = f"{year}, Battery Data Alliance"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_design",
    "myst_nb",
    "generate_plugins_doc",
]

# myst-nb: execute notebooks at build, cache results so unchanged
# notebooks are not re-run on subsequent builds.
nb_execution_mode = "cache"
nb_execution_timeout = 300
nb_execution_cache_path = os.path.abspath("_build/.jupyter_cache")

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": False,
}
napoleon_google_docstring = True
napoleon_numpy_docstring = True

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    # Example notebook data + helper module, not docs sources.
    "examples/in/**",
    "examples/out/**",
    "examples/reference/**",
    "examples/remote_sources.py",
]

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_title = project
