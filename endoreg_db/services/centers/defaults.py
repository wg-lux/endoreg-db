"""Study-independent center selection for local intake.

Remote transfers retain their authenticated node's center; they must not use
the receiving site's local default to infer source ownership.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.text import slugify

from endoreg_db.models.administration.app_settings import ApplicationSettings
from endoreg_db.models.administration.center.center import Center
from endoreg_db.utils.file_operations import advisory_file_lock
from endoreg_db.utils.paths import get_runtime_paths

logger = logging.getLogger(__name__)


def resolve_import_center(
    center_name: str | None, *, center_key: str | None = None
) -> Center:
    """Preserve a resolved identity while retaining explicit-name compatibility."""
    if center_key is not None:
        if not center_key.strip():
            raise ValueError("center_key must not be blank")
        center = Center.objects.get(center_key=center_key)
        if center_name and center_name.strip() != center.name:
            raise ValueError("Import center name does not match its stable key")
        return center
    if center_name and center_name.strip():
        return Center.objects.get(name=center_name.strip())
    return resolve_local_center()


def _setting(name: str, default: str = "") -> str:
    value: object = getattr(settings, name, os.environ.get(name, default))
    if not isinstance(value, str):
        raise ImproperlyConfigured(f"{name} must be a string")
    value = value.strip()
    if len(value) > 255:
        raise ImproperlyConfigured(f"{name} must not exceed 255 characters")
    return value


def resolve_local_center() -> Center:
    """Use an operator selection or provision the configured local identity.

    Provision lazily, after migrations, without loading clinical fixtures or
    activating a study. Unique center_key makes repeated/concurrent calls safe.
    The generated default is not persisted as an operator override.
    """
    # on PostgreSQL; uniqueness still protects independent hosts sharing a DB.
    with advisory_file_lock(lock_path=get_runtime_paths().locks / "local-center.lock"):
        return _resolve_local_center()


def _resolve_local_center() -> Center:
    selected = ApplicationSettings.objects.select_related("center").filter(pk=1).first()
    if selected is not None and selected.center is not None:
        return selected.center

    reference = _setting("LX_ANNOTATE_DEFAULT_CENTER")
    name = _setting("CENTER_NAME")
    if reference:
        existing = Center.objects.filter(center_key=reference).first()
        if existing is not None:
            return existing
        # Historical watcher configuration also accepted an existing center name.
        matches = list(Center.objects.filter(name=reference)[:2])
        if len(matches) > 1:
            raise ValueError("LX_ANNOTATE_DEFAULT_CENTER matches multiple center names")
        if matches:
            return matches[0]
        key = reference
    elif name:
        matches = list(Center.objects.filter(name=name)[:2])
        if len(matches) > 1:
            raise ValueError(
                "CENTER_NAME matches multiple centers; configure a center key"
            )
        if matches:
            return matches[0]
        key = slugify(name)
        if not key:
            raise ImproperlyConfigured(
                "CENTER_NAME requires LX_ANNOTATE_DEFAULT_CENTER"
            )
    else:
        key = "local-center"

    center, created = Center.objects.get_or_create(
        center_key=key,
        defaults={"name": name or (reference if reference else "Local Center")},
    )
    if not reference and name and center.name != name:
        raise ValueError(
            "Derived center key belongs to a different center; configure "
            "LX_ANNOTATE_DEFAULT_CENTER explicitly"
        )
    if created:
        logger.info("Provisioned local intake center: center_key=%s", center.center_key)
    return center
