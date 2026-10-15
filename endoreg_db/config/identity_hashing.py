"""Resolve the identity salt without implicit replacement or secret disclosure."""

from __future__ import annotations

import os
from pathlib import Path
from contextvars import ContextVar

from endoreg_db.config.secret_keyring import (
    SecretKeyring,
    configured_identity_keyring,
    read_secret_line,
    validate_salt_material,
)

from django.core.exceptions import ImproperlyConfigured

identity_keyring_snapshot: ContextVar[SecretKeyring | None] = ContextVar(
    "identity_keyring_snapshot"
)


def current_identity_keyring() -> SecretKeyring | None:
    try:
        return identity_keyring_snapshot.get()
    except LookupError:
        return configured_identity_keyring()


def validate_identity_salt(value: object) -> str:
    if not isinstance(value, str):
        raise ImproperlyConfigured("A non-default identity salt is required")
    try:
        return validate_salt_material(value)
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from None


def load_identity_salt(*, required: bool = False, allow_inline: bool = True) -> str:
    """Read the configured salt for export as the uppercase DJANGO_SALT setting.

    File contents take precedence. No salt is generated and no legacy salt is
    substituted. An absent optional setting remains empty and cannot be hashed.
    """
    ring = current_identity_keyring()
    if ring is not None:
        return validate_identity_salt(ring.active.decode("utf-8"))
    path = os.environ.get("DJANGO_SALT_FILE", "")
    if path:
        try:
            value = read_secret_line(Path(path))
        except ValueError:
            raise ImproperlyConfigured(
                "Unable to read a valid private identity salt file"
            ) from None
        return validate_identity_salt(value)
    inline = os.environ.get("DJANGO_SALT", "") if allow_inline else ""
    if inline:
        return validate_identity_salt(inline)
    if required:
        raise ImproperlyConfigured(
            "DJANGO_SALT_FILE is required; provision the established identity salt before starting production"
        )
    return ""
