"""Fail-closed source identity boundary shared by transfers, exports and HTTP."""

from __future__ import annotations

import json
import re
import csv
from itertools import chain
from collections.abc import Callable, Iterable, Mapping
from typing import BinaryIO, cast

from django.http import (
    FileResponse,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)

_IDENTITY_FIELD_MARKERS = (
    "patienthash",
    "examinationhash",
    "casehash",
    "examinerhash",
    "identityfingerprint",
    "patientpseudonym",
    "casepseudonym",
)
_IDENTITY_ERROR = "Patient identity hashes must remain on the source node"
_HTML_IDENTITY_FIELD = re.compile(
    rb"(?:patient|examination|case|examiner)[_ -]?(?:hash|pseudonym)|identity[_ -]?fingerprint",
    re.IGNORECASE,
)


def reject_identity_hash_fields(fields: Iterable[object]) -> None:
    """Reject identity-bearing field names, including shortened and renamed outputs."""
    for field in fields:
        if not isinstance(field, str):
            continue
        normalized = field.replace("_", "").replace("-", "").replace(" ", "").casefold()
        if normalized == "hash" or any(
            normalized.endswith(marker + suffix)
            for suffix in ("", "s", "es", "display")
            for marker in _IDENTITY_FIELD_MARKERS
        ):
            raise ValueError(_IDENTITY_ERROR)


def reject_transferred_identity_hashes(payload: object) -> None:
    """Reject source identity hashes anywhere in a payload without logging values."""
    if isinstance(payload, Mapping):
        fields = cast(Mapping[object, object], payload)
        reject_identity_hash_fields(fields)
        for value in fields.values():
            reject_transferred_identity_hashes(value)
    elif isinstance(payload, str):
        reject_identity_hash_fields((payload,))
    elif isinstance(payload, (list, tuple)):
        for value in cast(list[object] | tuple[object, ...], payload):
            reject_transferred_identity_hashes(value)


class HeaderValidatedCsvResponse(StreamingHttpResponse):
    """Stream fixed export columns after checking their header before publication."""

    def __init__(self, lines: Iterable[str]) -> None:
        iterator = iter(lines)
        header = next(iterator, None)
        if header is None:
            raise ValueError("CSV export requires a header")
        columns = next(csv.reader([header]))
        if not columns:
            raise ValueError("CSV export requires named columns")
        reject_identity_hash_fields(columns)
        super().__init__(
            chain((header,), iterator), content_type="text/csv; charset=utf-8"
        )


class IdentityValidatedJsonFileResponse(FileResponse):
    """Validate an explicitly selected JSON artifact before streaming its same handle."""

    _json_file: BinaryIO

    def __init__(self, file: BinaryIO, *, filename: str) -> None:
        self._json_file = file
        super().__init__(
            file, as_attachment=True, filename=filename, content_type="application/json"
        )

    def validate_identity_fields(self) -> None:
        self._json_file.seek(0)
        reject_transferred_identity_hashes(json.load(self._json_file))
        self._json_file.seek(0)


class IdentityHashResponseMiddleware:
    """Withhold unsafe responses; never buffer or iterate deferred media streams."""

    get_response: Callable[[HttpRequest], HttpResponse | StreamingHttpResponse]

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse | StreamingHttpResponse],
    ) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse | StreamingHttpResponse:
        response = self.get_response(request)
        content_type = response.get("Content-Type", "").split(";", 1)[0].strip().lower()
        structured = content_type.endswith(("json", "xml")) or content_type.startswith(
            "text/"
        )
        try:
            reject_identity_hash_fields(response.headers)
            if isinstance(response, StreamingHttpResponse):
                if isinstance(response, IdentityValidatedJsonFileResponse):
                    response.validate_identity_fields()
                elif structured and not isinstance(
                    response, HeaderValidatedCsvResponse
                ):
                    raise ValueError(
                        "Deferred metadata cannot be inspected before disclosure"
                    )
                return response
            if response.content and content_type.endswith("json"):
                reject_transferred_identity_hashes(json.loads(response.content))
            elif structured and _HTML_IDENTITY_FIELD.search(response.content):
                raise ValueError(_IDENTITY_ERROR)
        except (ValueError, RecursionError):
            rejection = JsonResponse(
                {"code": "identity_hash_response_blocked", "error": _IDENTITY_ERROR},
                status=409,
            )
            # Transfer resource ownership without firing request_finished inside
            # middleware. The replacement response finishes the request once.
            closers = cast(
                list[Callable[[], object]], getattr(response, "_resource_closers")
            )
            rejection_closers = cast(
                list[Callable[[], object]], getattr(rejection, "_resource_closers")
            )
            rejection_closers.extend(closers)
            closers.clear()
            return rejection
        return response
