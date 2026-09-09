from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import cast
from uuid import UUID

import pytest
from _pytest.logging import LogCaptureFixture
from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.test import AsyncClient, RequestFactory, override_settings
from django.urls import path

from endoreg_db.request_logging import RequestLoggingMiddleware
from endoreg_db.utils.structured_logging import (
    StructuredJsonFormatter,
    emit_structured_event,
)


pytestmark = pytest.mark.no_db
logger = logging.getLogger("endoreg_db.requests.tests")


def _events(caplog: LogCaptureFixture) -> list[Mapping[str, object]]:
    return [
        cast(Mapping[str, object], getattr(record, "structured_event"))
        for record in caplog.records
        if hasattr(record, "structured_event")
    ]


@pytest.mark.parametrize("status_code", [200, 204, 400, 401, 403, 500, 503])
def test_response_and_logs_share_server_identity_without_request_payload(
    caplog: LogCaptureFixture, status_code: int
) -> None:
    request = RequestFactory().post(
        "/clinical-name/?token=private-query",
        {"patient": "private-body"},
        HTTP_X_REQUEST_ID="private-client-identity",
        HTTP_AUTHORIZATION="Bearer private-credential",
    )

    def respond(_request: HttpRequest) -> HttpResponse:
        emit_structured_event(logger, "view.finished", request_id="forged")
        return HttpResponse(status=status_code)

    with caplog.at_level(logging.INFO, logger="endoreg_db.requests"):
        response = RequestLoggingMiddleware(respond)(request)
        emit_structured_event(logger, "outside_request")

    request_id = response["X-Request-ID"]
    assert UUID(request_id).version == 4
    events = _events(caplog)
    assert events[0]["request_id"] == request_id
    assert events[1]["request_id"] == request_id
    assert events[1]["status_code"] == status_code
    assert events[1]["method"] == "POST"
    assert "request_id" not in events[2]
    # Deferred formatting must retain the identity captured at event emission.
    assert (
        json.loads(StructuredJsonFormatter().format(caplog.records[0]))["request_id"]
        == request_id
    )
    assert "private-" not in caplog.text
    assert "clinical-name" not in caplog.text
    assert "forged" not in caplog.text


def test_exception_propagates_and_clears_context(caplog: LogCaptureFixture) -> None:
    error = RuntimeError("clinical-exception-content")

    def fail(_request: HttpRequest) -> HttpResponse:
        raise error

    with caplog.at_level(logging.INFO, logger="endoreg_db.requests"):
        with pytest.raises(RuntimeError) as caught:
            RequestLoggingMiddleware(fail)(RequestFactory().get("/"))
        emit_structured_event(logger, "after_failure")

    assert caught.value is error
    events = _events(caplog)
    assert events[0]["status_code"] == 500
    assert events[0]["outcome"] == "failed"
    assert "request_id" not in events[1]
    assert "clinical-exception-content" not in caplog.text


def test_concurrent_requests_keep_independent_log_contexts(
    caplog: LogCaptureFixture,
) -> None:
    barrier = Barrier(2, timeout=10)

    def respond(request: HttpRequest) -> HttpResponse:
        barrier.wait()
        emit_structured_event(logger, "concurrent_view", method=request.method)
        return HttpResponse()

    middleware = RequestLoggingMiddleware(respond)
    with caplog.at_level(logging.INFO, logger="endoreg_db.requests"):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(middleware, RequestFactory().get("/"))
            second = executor.submit(middleware, RequestFactory().post("/"))
            expected = {
                "GET": first.result(timeout=10)["X-Request-ID"],
                "POST": second.result(timeout=10)["X-Request-ID"],
            }

    assert len(set(expected.values())) == 2
    for event in _events(caplog):
        method = event["method"]
        assert isinstance(method, str)
        assert event["request_id"] == expected[method]


def test_streaming_response_remains_lazy_and_closable(
    caplog: LogCaptureFixture,
) -> None:
    consumed: list[str] = []

    def chunks() -> Iterator[bytes]:
        try:
            consumed.append("started")
            yield b"first"
            yield b"second"
        finally:
            consumed.append("closed")

    response = StreamingHttpResponse(chunks())

    def respond(_request: HttpRequest) -> StreamingHttpResponse:
        return response

    with caplog.at_level(logging.INFO, logger="endoreg_db.requests"):
        returned = RequestLoggingMiddleware(respond)(RequestFactory().get("/"))
        emit_structured_event(logger, "after_response_preparation")

    assert returned is response
    assert consumed == []
    assert "request_id" not in _events(caplog)[-1]
    content = response.streaming_content
    assert isinstance(content, Iterator)
    assert list(content) == [b"first", b"second"]
    response.close()
    assert consumed == ["started", "closed"]


async def async_view(_request: HttpRequest) -> HttpResponse:
    await asyncio.sleep(0)
    emit_structured_event(logger, "async_view")
    return HttpResponse(status=204)


urlpatterns = [path("request-test/", async_view)]


@override_settings(
    ROOT_URLCONF=__name__,
    ALLOWED_HOSTS=["testserver"],
    MIDDLEWARE=["endoreg_db.request_logging.RequestLoggingMiddleware"],
)
def test_django_async_adapter_preserves_correlation(caplog: LogCaptureFixture) -> None:
    async def request() -> str:
        response = await AsyncClient().get("/request-test/")
        assert response.status_code == 204
        return response["X-Request-ID"]

    with caplog.at_level(logging.INFO, logger="endoreg_db.requests"):
        request_id = asyncio.run(request())
    events = _events(caplog)
    assert len(events) == 2
    assert all(event["request_id"] == request_id for event in events)


def test_middleware_covers_security_redirects() -> None:
    assert (
        settings.MIDDLEWARE[0] == "endoreg_db.request_logging.RequestLoggingMiddleware"
    )
