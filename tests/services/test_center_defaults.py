from __future__ import annotations

# pyright: reportPrivateUsage=false

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings
from django.db import connections
from rest_framework.exceptions import ValidationError

from endoreg_db.models import ApplicationSettings, Center, NetworkNode
from endoreg_db.serializers.hub.transfer_job import TransferJobCreateSerializer
from endoreg_db.services.center_defaults import (
    resolve_import_center,
    resolve_local_center,
)
from endoreg_db.services.hub.ingest import resolve_default_center

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def local_settings() -> Iterator[None]:
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="", CENTER_NAME=""):
        yield


def test_fresh_database_without_study_or_fixtures_provisions_once() -> None:
    assert not Center.objects.exists()
    center = resolve_local_center()
    assert (center.center_key, center.name) == ("local-center", "Local Center")
    assert resolve_default_center() == center
    assert Center.objects.count() == 1
    assert not ApplicationSettings.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_intake_reuses_one_center() -> None:
    barrier = Barrier(2)

    def resolve() -> int:
        try:
            barrier.wait(timeout=10)
            return int(resolve_local_center().pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(resolve)
        second = executor.submit(resolve)
        assert first.result(timeout=20) == second.result(timeout=20)
    assert Center.objects.filter(center_key="local-center").count() == 1


def test_unrelated_first_center_is_not_a_default() -> None:
    other = Center.objects.create(name="Unrelated study center")
    assert resolve_local_center() != other


def test_django_settings_override_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LX_ANNOTATE_DEFAULT_CENTER", "wrong-env-center")
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="site-a", CENTER_NAME="Site A"):
        center = resolve_local_center()
    assert (center.center_key, center.name) == ("site-a", "Site A")


def test_operator_selection_overrides_configuration_without_reassignment() -> None:
    selected = Center.objects.create(name="Selected", center_key="selected")
    ApplicationSettings.objects.create(center=selected)
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="new-site"):
        assert resolve_local_center() == selected
    assert Center.objects.count() == 1


def test_configured_key_preserves_existing_name() -> None:
    center = Center.objects.create(name="Reviewed Site", center_key="site-a")
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="site-a", CENTER_NAME="Other"):
        assert resolve_local_center() == center
    center.refresh_from_db()
    assert center.name == "Reviewed Site"


def test_legacy_name_reuses_existing_center() -> None:
    center = Center.objects.create(name="Legacy", center_key="stable-key")
    with override_settings(CENTER_NAME="Legacy"):
        assert resolve_local_center() == center
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="Legacy"):
        assert resolve_local_center() == center


def test_ambiguous_name_requires_explicit_key() -> None:
    Center.objects.create(name="Shared", center_key="one")
    Center.objects.create(name="Shared", center_key="two")
    with override_settings(CENTER_NAME="Shared"), pytest.raises(ValueError):
        resolve_local_center()


def test_derived_key_collision_does_not_select_an_unrelated_center() -> None:
    original = Center.objects.create(name="Reviewed Center", center_key="site-a")
    with (
        override_settings(CENTER_NAME="Site A"),
        pytest.raises(ValueError, match="different center"),
    ):
        resolve_local_center()
    original.refresh_from_db()
    assert original.name == "Reviewed Center"
    assert Center.objects.count() == 1


def test_hub_omitted_center_uses_source_node_not_recipient_default() -> None:
    local = resolve_local_center()
    source = Center.objects.create(name="Remote Site", center_key="remote-site")
    node = NetworkNode.objects.create(node_key="remote", owning_center=source)
    serializer = TransferJobCreateSerializer()
    # Exercise the same ownership resolution used for real transfer registration.
    resolved = serializer._resolve_source_center({}, source_node=node)
    assert resolved == source
    assert resolved != local
    serializer._validate_source_center_ownership(
        source_node=node, source_center=resolved
    )
    with pytest.raises(ValidationError):
        serializer._validate_source_center_ownership(
            source_node=node, source_center=local
        )


def test_video_import_without_center_passes_local_default_to_importer(
    tmp_path: Path,
) -> None:
    from endoreg_db.services.video_files.imports import create_video_file_from_path

    with patch("endoreg_db.services.video_files._imports._create_from_file") as create:
        create_video_file_from_path(tmp_path / "video.mp4", raw_video_hash="a" * 64)
    # Resolution belongs to the persistence boundary; do not discard the identity
    # by replacing an omitted center with its non-unique display name here.
    assert create.call_args.kwargs["center_name"] == ""


def test_pdf_import_without_center_reaches_storage_with_local_default(
    tmp_path: Path,
) -> None:
    from endoreg_db.services.raw_pdf_files.imports import create_raw_pdf_file_from_path

    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    model = Mock()
    model.objects.filter.return_value.first.return_value = None
    model.return_value.file.save_local.side_effect = RuntimeError("storage sentinel")
    with (
        patch(
            "endoreg_db.services.raw_pdf_files.imports._raw_pdf_model",
            return_value=model,
        ),
        pytest.raises(RuntimeError, match="storage sentinel"),
    ):
        create_raw_pdf_file_from_path(source)
    assert model.call_args.kwargs["center"] == Center.objects.get(
        center_key="local-center"
    )


def test_explicit_pdf_center_name_is_not_reinterpreted_as_key(tmp_path: Path) -> None:
    from endoreg_db.services.raw_pdf_files.imports import create_raw_pdf_file_from_path

    study = Center.objects.create(name="study-site", center_key="reviewed-study-key")
    Center.objects.create(name="Unrelated", center_key="study-site")
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    model = Mock()
    model.objects.filter.return_value.first.return_value = None
    model.return_value.file.save_local.side_effect = RuntimeError("storage sentinel")
    with (
        patch(
            "endoreg_db.services.raw_pdf_files.imports._raw_pdf_model",
            return_value=model,
        ),
        pytest.raises(RuntimeError, match="storage sentinel"),
    ):
        create_raw_pdf_file_from_path(source, center_name="study-site")
    assert model.call_args.kwargs["center"] == study
    with pytest.raises(ValueError, match="not found"):
        create_raw_pdf_file_from_path(source, center_name="unknown-study")
    assert Center.objects.count() == 2


@pytest.mark.parametrize("selection", ["setting", "operator"])
def test_key_selection_survives_duplicate_center_names(selection: str) -> None:
    Center.objects.create(name="Shared", center_key="unrelated")
    selected = Center.objects.create(name="Shared", center_key="selected")
    if selection == "operator":
        ApplicationSettings.objects.create(center=selected)
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="selected"):
        assert resolve_import_center("") == selected
        assert resolve_import_center("Shared", center_key="selected") == selected
    with pytest.raises(Center.MultipleObjectsReturned):
        resolve_import_center("Shared")
    with pytest.raises(Center.DoesNotExist):
        resolve_import_center("Shared", center_key="missing")
    with pytest.raises(ValueError, match="does not match"):
        resolve_import_center("Different", center_key="selected")


def test_report_pipeline_persists_selected_key_with_duplicate_names(
    tmp_path: Path,
) -> None:
    from endoreg_db.import_files.report_import_service import ReportImportService
    from endoreg_db.import_files.file_storage.create_report_file import (
        create_or_retrieve_report_file,
    )

    Center.objects.create(name="Shared", center_key="unrelated")
    selected = Center.objects.create(name="Shared", center_key="selected")
    source = tmp_path / "report.pdf"
    source.write_bytes(
        ReportImportService._render_single_page_pdf("Center identity test")
    )
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="selected"):
        context = ReportImportService()._create_import_context(source, "")
    # Changing the default after context creation must not reroute this import.
    with override_settings(LX_ANNOTATE_DEFAULT_CENTER="unrelated"):
        report, _, _ = create_or_retrieve_report_file(context)
    report.refresh_from_db()
    assert report.center_id == selected.pk


def test_transferred_video_with_same_name_cannot_cross_center_key(
    tmp_path: Path,
) -> None:
    from endoreg_db.import_files.video_import_service import VideoImportService
    from endoreg_db.import_files.context.import_context import ImportContext
    from endoreg_db.models.media.video.video_file import VideoFile

    other = Center.objects.create(name="Shared", center_key="other")
    context = ImportContext(
        file_path=tmp_path / "video.mp4", center_name="Shared", center_key="selected"
    )
    with pytest.raises(ValueError, match="different center"):
        VideoImportService._ensure_duplicate_streaming(context, VideoFile(center=other))
