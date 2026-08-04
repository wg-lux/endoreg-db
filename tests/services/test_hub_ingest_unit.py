from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import override_settings

import pytest

from endoreg_db.models.administration.center.center import Center
from endoreg_db.services.hub import ingest


class _Anonymous:
    is_authenticated = False


class _Authenticated:
    is_authenticated = True


def _none_center_result(**_kwargs: object) -> tuple[None, None]:
    return (None, None)


def test_is_celery_broker_connection_error_detects_nested_cause() -> None:
    root = RuntimeError("outer")
    root.__cause__ = ConnectionRefusedError("connection refused")
    assert ingest._is_celery_broker_connection_error(root) is True  # pyright: ignore[reportPrivateUsage]

    assert (
        ingest._is_celery_broker_connection_error(RuntimeError("no broker signal"))  # pyright: ignore[reportPrivateUsage]
        is False
    )


def test_normalized_upload_provenance_preserves_explicit_values() -> None:
    center = Center(name="main", display_name="Main Center", center_key="main")
    provenance = ingest._normalized_upload_provenance(  # pyright: ignore[reportPrivateUsage]
        ingest_mode="upload",
        source_system="api",
        content_hash="abc",
        source_center=center,
        storage_class="local",
        storage_tier="hot",
        retention_policy="default",
        processing_provenance={"entrypoint": "existing", "custom_marker": "x"},
    )
    assert provenance.get("entrypoint") == "existing"
    assert provenance.get("ingest_mode") == "upload"
    assert provenance.get("source_system") == "api"
    assert provenance.get("content_hash") == "abc"
    assert provenance.get("storage_class") == "local"
    assert provenance.get("storage_tier") == "hot"
    assert provenance.get("retention_policy") == "default"
    assert provenance.get("source_center_key") == center.center_key


def test_compute_uploaded_file_content_hash_uses_uploaded_file_chunks() -> None:
    uploaded = SimpleUploadedFile(
        name="blob.bin",
        content=b"chunk-1-chunk-2",
        content_type="application/octet-stream",
    )
    assert (
        ingest._compute_uploaded_file_content_hash(uploaded)  # pyright: ignore[reportPrivateUsage]
        == "da65da2b47cba2b28aa8d6859c2b1dddcf1300da9b65c5ca8e1ad3191a57bf9d"
    )


def test_is_retryable_db_lock_error_matches_known_signatures() -> None:
    assert ingest._is_retryable_db_lock_error(  # pyright: ignore[reportPrivateUsage]
        OperationalError("database is locked by other process")
    )
    assert not ingest._is_retryable_db_lock_error(  # pyright: ignore[reportPrivateUsage]
        OperationalError("permanent failure")
    )


@pytest.mark.django_db
def test_resolve_declared_upload_center_prefers_matching_key_and_name() -> None:
    center = Center.objects.create(
        name="berlin",
        display_name="Berlin",
        center_key="c-b",
    )

    resolved, error = ingest.resolve_declared_upload_center(
        center_key="c-b",
        center_name="Berlin",
    )

    assert error is None
    assert resolved == center


@pytest.mark.django_db
def test_resolve_declared_upload_center_conflict_and_unknown() -> None:
    Center.objects.create(name="munich", display_name="Munich", center_key="M-01")
    Center.objects.create(name="hamburg", display_name="Hamburg", center_key="H-02")

    resolved, error = ingest.resolve_declared_upload_center(
        center_key="M-01",
        center_name="Hamburg",
    )
    assert resolved == Center.objects.get(center_key="M-01")
    assert error is None

    resolved_none, error_unknown = ingest.resolve_declared_upload_center(
        center_key="M-99"
    )
    assert resolved_none is None
    assert error_unknown == "Unknown center_key: M-99"


def test_resolve_allowed_center_id_is_type_strict() -> None:
    assert ingest.resolve_allowed_center_id(None) is None
    assert ingest.resolve_allowed_center_id(AnonymousUser()) is None
    assert ingest.resolve_allowed_center_id(_Anonymous()) is None
    assert ingest.resolve_allowed_center_id(_Authenticated()) == -1


@pytest.mark.django_db
@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_resolve_api_upload_context_rejects_anonymous_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingest, "local_study_server_mode_enabled", lambda: False)
    monkeypatch.setattr(ingest, "strict_center_upload_mode_enabled", lambda: True)
    monkeypatch.setattr(ingest, "resolve_declared_upload_center", _none_center_result)

    with patch("endoreg_db.services.hub.ingest.emit_hub_audit_event") as emit_audit:
        source_center, allowed_center_id, error, payload = (
            ingest.resolve_api_upload_context(
                user=SimpleNamespace(is_authenticated=False),
                center_key=None,
                center_name=None,
            )
        )

    assert source_center is None
    assert allowed_center_id is None
    assert error == "Authentication is required for center-scoped API uploads."
    assert payload["hub_mode"] is True
    emit_audit.assert_not_called()


@pytest.mark.django_db
def test_resolve_api_upload_context_allows_anonymous_debug_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(
        name="debug-upload-center",
        display_name="Debug Upload Center",
        center_key="debug-upload-center",
    )
    monkeypatch.setattr(ingest, "hub_mode_enabled", lambda: False)
    monkeypatch.setattr(ingest, "local_study_server_mode_enabled", lambda: False)
    monkeypatch.setattr(ingest, "strict_center_upload_mode_enabled", lambda: False)
    monkeypatch.setattr(ingest, "is_debug_mode", lambda: True)

    source_center, allowed_center_id, error, payload = (
        ingest.resolve_api_upload_context(
            user=AnonymousUser(),
            center_key=center.center_key,
        )
    )

    assert source_center == center
    assert allowed_center_id is None
    assert error is None
    assert payload["hub_mode"] is False
