import sys
import os
import django
from typing import List

# Add the project root directory (containing 'endoreg_db' and 'tests') to the path
project_root = os.path.abspath('../../')
print(f"--- Adding to sys.path: {project_root}") # DEBUG LINE
sys.path.insert(0, project_root)
# print(f"--- Current sys.path: {sys.path}") # Optional: print the whole path

DJANGO_SETTINGS_MODULE = "doc_settings"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)

print("--- Running django.setup()...") # DEBUG LINE
try:
    django.setup()
    print("--- django.setup() finished successfully.") # DEBUG LINE
except Exception as e:
    print(f"--- django.setup() FAILED: {e}") # DEBUG LINE

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'EndoReg-DB'
copyright = '2025, AG-Lux'
author = 'AG-Lux'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',  # Core extension for pulling docstrings
    'sphinx.ext.napoleon', # If using Google or NumPy style docstrings
    'sphinx.ext.viewcode', # Adds links to source code
    'sphinx.ext.intersphinx', # Link to other projects' docs (e.g., Python, Django)
]

autodoc_typehints = "description"
autoclass_content = "class" # Or 'class' if 'init' causes issues

autodoc_default_options = {
    'members': True,
    'undoc-members': False, # Adjust as needed
    'show-inheritance': True,
    'no-value': True,  # Add this line
}

# Example intersphinx mapping
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'django': ('https://docs.djangoproject.com/en/stable/', 'https://docs.djangoproject.com/en/stable/_objects/'),
}

templates_path = ['_templates']
exclude_patterns:List[str] = []



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme' 
html_static_path = ['_static']
