"""Compatibility alias for :mod:`endoreg_db.services.jobs.video_task_cleanup`."""

from __future__ import annotations

from importlib import import_module
import sys

_alias_name = __name__
_module = import_module("endoreg_db.services.jobs.video_task_cleanup")
globals().update(_module.__dict__)
sys.modules[_alias_name] = _module
