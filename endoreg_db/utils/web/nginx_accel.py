# endoreg_db/utils/web/nginx_accel.py

from __future__ import annotations

import posixpath
from pathlib import Path

from django.http import HttpResponse, HttpResponseBase

from endoreg_db.config.env import (
    get_protected_media_url,
    nginx_offload_enabled as env_nginx_offload_enabled,
)
from endoreg_db.utils.filesystem.paths import (
    normalize_protected_media_relative_path,
    to_protected_media_relative,
)
from endoreg_db.utils.storage.streaming import add_cors_headers


def nginx_protected_url() -> str:
    return get_protected_media_url()


def nginx_offload_enabled() -> bool:
    return env_nginx_offload_enabled()


def build_nginx_accel_response(
    *,
    protected_relative_path: str,
    content_type: str,
    filename: str | None = None,
    disposition: str | None = None,
    frontend_origin: str | None = None,
    buffering: str = "no",
    accept_ranges: bool = True,
) -> HttpResponseBase:
    safe_relative_path = normalize_protected_media_relative_path(
        protected_relative_path
    )
    response: HttpResponseBase = HttpResponse()
    response["Content-Type"] = content_type
    response["X-Accel-Redirect"] = posixpath.join(
        nginx_protected_url().rstrip("/"),
        safe_relative_path,
    )
    response["X-Accel-Buffering"] = buffering
    if accept_ranges:
        response["Accept-Ranges"] = "bytes"
    if disposition is not None and filename is not None:
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    if frontend_origin is not None:
        response = add_cors_headers(response, frontend_origin)
    return response


def build_nginx_accel_response_for_path(
    *,
    path: Path,
    content_type: str,
    filename: str | None = None,
    disposition: str | None = None,
    frontend_origin: str | None = None,
    buffering: str = "no",
    accept_ranges: bool = True,
) -> HttpResponseBase:
    relative_path = to_protected_media_relative(path.resolve())
    return build_nginx_accel_response(
        protected_relative_path=str(relative_path),
        content_type=content_type,
        filename=filename,
        disposition=disposition,
        frontend_origin=frontend_origin,
        buffering=buffering,
        accept_ranges=accept_ranges,
    )
