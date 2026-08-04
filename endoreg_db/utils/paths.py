"""Compatibility imports for legacy path imports.

New code should import from :mod:`endoreg_db.utils.filesystem.paths`.
"""

from __future__ import annotations

from endoreg_db.utils._compat import reexport_public_module

reexport_public_module("endoreg_db.utils.filesystem.paths", globals())
