"""Compatibility wrapper for Django's DEBUG-only static URL helper."""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlsplit

try:
    from django.conf.urls.static import static
except ImportError:
    from django.core.exceptions import ImproperlyConfigured
    from django.http import HttpResponseBase
    from django.urls import URLPattern
    from django.urls import re_path
    from django.views.static import serve

    def static(
        prefix: str,
        view: Callable[..., HttpResponseBase] = serve,
        **kwargs: Any,
    ) -> list[URLPattern]:
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
