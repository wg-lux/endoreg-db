#!/usr/bin/env python3
"""
Lightweight startup smoke check for Django import stability.

Validates that:
- Django apps can initialize (`django.setup()`)
- Root URL module imports successfully
- URL resolver can build URL patterns

This catches broad import-chain regressions (e.g. a single bad view import
breaking the whole API startup) early in pre-commit/CI.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "endoreg_db.config.settings.test")
    os.environ.setdefault("DJANGO_DEBUG", "1")
    os.environ.setdefault("LX_ANNOTATE_ENCRYPTED_DATA_DIR", "data")
    os.environ.setdefault("STORAGE_DIR", "data/storage")
    os.environ.setdefault("IO_DIR", "data")

    try:
        import django

        django.setup()

        from django.urls import get_resolver

        resolver = get_resolver("endoreg_db.root_urls")
        patterns = getattr(resolver, "url_patterns", [])
        print(f"startup-smoke: ok ({len(patterns)} root url pattern(s))")
        return 0
    except Exception as exc:
        print(f"startup-smoke: failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
