"""Correlate backend request handling without reading or buffering response bodies."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    request_log_context,
)


logger = logging.getLogger("endoreg_db.requests")
_HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)


class RequestLoggingMiddleware:
    """Measure response preparation, not deferred streaming or proxy transfer.

    Incoming correlation headers are deliberately not trusted. Django adapts
    this synchronous middleware for asynchronous views; context variables keep
    simultaneous requests isolated across its thread adapters.
    """

    get_response: Callable[[HttpRequest], HttpResponseBase]

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        with request_log_context() as request_id:
            started_at = time.monotonic()
            status_code = 500
            try:
                response = self.get_response(request)
                status_code = response.status_code
                response["X-Request-ID"] = request_id
                return response
            finally:
                emit_structured_event(
                    logger,
                    "http.response_preparation_finished",
                    level=logging.ERROR if status_code >= 500 else logging.INFO,
                    operation="http_request",
                    method=request.method
                    if request.method in _HTTP_METHODS
                    else "UNKNOWN",
                    status_code=status_code,
                    outcome="failed"
                    if status_code >= 500
                    else "rejected"
                    if status_code >= 400
                    else "completed",
                    duration_seconds=time.monotonic() - started_at,
                )
