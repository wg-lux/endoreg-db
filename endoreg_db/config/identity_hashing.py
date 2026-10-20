"""Resolve the identity salt without implicit replacement or secret disclosure."""

from __future__ import annotations

import os
from pathlib import Path
from contextvars import ContextVar

from endoreg_db.config.secret_keyring import (
    SecretKeyring,
    configured_identity_keyring,
    read_secret_line,
)

from django.core.exceptions import ImproperlyConfigured

identity_keyring_snapshot: ContextVar[SecretKeyring | None] = ContextVar(
    "identity_keyring_snapshot"
)


def validate_identity_salt(value: object) -> str:
    """Expose invalid application settings as redacted configuration errors."""
    # The utility package re-exports hashing helpers which import this module.
    # Resolve its validator only after configuration module initialization.
    from endoreg_db.utils.verify_salt import (
        validate_identity_salt as _validate_identity_salt,
    )

    try:
        return _validate_identity_salt(value)
    except ValueError:
        raise ImproperlyConfigured("Invalid identity salt configuration") from None


def current_identity_keyring() -> SecretKeyring | None:
    try:
        return identity_keyring_snapshot.get()
    except LookupError:
        return configured_identity_keyring()


def load_identity_salt(*, required: bool = False, allow_inline: bool = True) -> str:
    """Read the configured salt for export as the uppercase DJANGO_SALT setting.

    File contents take precedence. No salt is generated and no legacy salt is
    substituted. An absent optional setting remains empty and cannot be hashed.
    """
    ring = current_identity_keyring()
    if ring is not None:
        return validate_identity_salt(ring.active)
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
