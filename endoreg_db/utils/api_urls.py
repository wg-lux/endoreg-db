from __future__ import annotations

ENDOREG_API_PREFIX = "/endoreg-api/"
ENDOREG_API_COMPATIBILITY_PREFIX = "/api/"
DTYPES_API_PREFIX = "/dtypes-api/"
DTYPES_API_COMPATIBILITY_PREFIX = "/base_api/"


def normalize_public_prefix(prefix: str) -> str:
    mount = prefix.strip("/")
    if not mount:
        raise ValueError("API prefix must not be empty")
    return f"/{mount}/"


def django_path_prefix(prefix: str) -> str:
    return normalize_public_prefix(prefix).strip("/") + "/"


def build_prefixed_path(prefix: str, relative_path: str) -> str:
    normalized_prefix = normalize_public_prefix(prefix)
    normalized_path = relative_path.lstrip("/")
    if not normalized_path:
        return normalized_prefix
    return f"{normalized_prefix}{normalized_path}"


def endoreg_api_path(relative_path: str) -> str:
    return build_prefixed_path(ENDOREG_API_PREFIX, relative_path)


def dtypes_api_path(relative_path: str) -> str:
    return build_prefixed_path(DTYPES_API_PREFIX, relative_path)
