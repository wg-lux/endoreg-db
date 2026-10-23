from __future__ import annotations

import pytest

from endoreg_db.utils.api_urls import (
    DTYPES_API_PREFIX,
    ENDOREG_API_COMPATIBILITY_PREFIX,
    ENDOREG_API_PREFIX,
    build_prefixed_path,
    django_path_prefix,
    dtypes_api_path,
    endoreg_api_path,
    normalize_public_prefix,
)


def test_api_prefix_constants_are_explicit_mount_contract() -> None:
    assert ENDOREG_API_PREFIX == "/endoreg-api/"
    assert ENDOREG_API_COMPATIBILITY_PREFIX == "/api/"
    assert DTYPES_API_PREFIX == "/dtypes-api/"


@pytest.mark.parametrize(
    ("raw_prefix", "normalized"),
    [
        ("endoreg-api", "/endoreg-api/"),
        ("/endoreg-api", "/endoreg-api/"),
        ("endoreg-api/", "/endoreg-api/"),
        ("/endoreg-api/", "/endoreg-api/"),
    ],
)
def test_normalize_public_prefix(raw_prefix: str, normalized: str) -> None:
    assert normalize_public_prefix(raw_prefix) == normalized


def test_normalize_public_prefix_rejects_empty_mount() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_public_prefix("/")


def test_django_path_prefix_has_no_leading_slash() -> None:
    assert django_path_prefix(ENDOREG_API_PREFIX) == "endoreg-api/"


def test_build_prefixed_path_joins_relative_paths() -> None:
    assert build_prefixed_path(ENDOREG_API_PREFIX, "") == ENDOREG_API_PREFIX
    assert (
        build_prefixed_path(ENDOREG_API_PREFIX, "media/videos/7/stream/")
        == "/endoreg-api/media/videos/7/stream/"
    )
    assert (
        build_prefixed_path(ENDOREG_API_PREFIX, "/media/videos/7/stream/")
        == "/endoreg-api/media/videos/7/stream/"
    )


def test_surface_specific_path_helpers() -> None:
    assert endoreg_api_path("media/videos/7/stream/") == (
        "/endoreg-api/media/videos/7/stream/"
    )
    assert dtypes_api_path("lookups/") == "/dtypes-api/lookups/"
