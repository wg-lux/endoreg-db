"""Resolve the identity salt without implicit replacement or secret disclosure."""

from __future__ import annotations

import os
import stat
from contextvars import ContextVar

from endoreg_db.config.secret_keyring import SecretKeyring, configured_identity_keyring

from django.core.exceptions import ImproperlyConfigured

identity_keyring_snapshot: ContextVar[SecretKeyring | None] = ContextVar(
    "identity_keyring_snapshot", default=None
)


def current_identity_keyring() -> SecretKeyring | None:
    return identity_keyring_snapshot.get() or configured_identity_keyring()


def validate_identity_salt(value: object) -> str:
    if not isinstance(value, str) or not value or value == "default_salt":
        raise ImproperlyConfigured("A non-default identity salt is required")
    if value != value.strip() or "\n" in value or "\r" in value:
        raise ImproperlyConfigured(
            "Identity salt must be a single nonblank line without surrounding whitespace"
        )
    return value


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
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as secret_file:
                info = os.fstat(secret_file.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077
                    or info.st_uid not in {0, os.geteuid()}
                ):
                    raise ImproperlyConfigured(
                        "Identity salt file must be private and owned by the service user or root"
                    )
                raw = secret_file.read(4097)
            if len(raw) > 4096:
                raise ImproperlyConfigured(
                    "Identity salt file exceeds the supported size"
                )
            value = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        except (OSError, UnicodeError):
            raise ImproperlyConfigured(
                "Unable to read the configured identity salt file"
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
