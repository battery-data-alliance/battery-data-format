import os
import sys
from datetime import datetime

# Ensure src/ is on path for autodoc
sys.path.insert(0, os.path.abspath("../src"))

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
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": False,
}
napoleon_google_docstring = True
napoleon_numpy_docstring = True

templates_path = ["_templates"]
exclude_patterns = ["_build"]

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_title = project
