"""Compatibility wrapper for Django's DEBUG-only static URL helper."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

try:
    from django.conf.urls.static import static
except ImportError:
    from django.core.exceptions import ImproperlyConfigured
    from django.urls import re_path
    from django.views.static import serve

    def static(prefix: str, view=serve, **kwargs):  # type: ignore[no-redef]
        if not prefix:
            raise ImproperlyConfigured("Empty static prefix not permitted")

        if urlsplit(prefix).netloc:
            return []

        return [
            re_path(
                r"^%s(?P<path>.*)$" % re.escape(prefix.lstrip("/")),
                view,
                kwargs=kwargs,
            ),
        ]
