from __future__ import annotations

# pyright: reportUnknownMemberType=false

from types import SimpleNamespace
from typing import cast

import pytest

from endoreg_db.models import ApplicationSettings, EndoscopyProcessor, VideoFile
from endoreg_db.services.jobs.video_reimport_jobs import _processor_name  # pyright: ignore[reportPrivateUsage]
from endoreg_db.services.video_files.processor_resolution import (
    resolve_processor_name_for_import,
)
from endoreg_db.services.video_files import get_video_import_context_names

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "processor_name", [None, "", " ", "Unknown", "undefined", "NONE", "null"]
)
def test_resolve_processor_name_uses_application_default_for_unknown(
    processor_name: str | None,
) -> None:
    processor = EndoscopyProcessor.objects.create(name="configured_default_processor")
    settings = ApplicationSettings.get_solo()
    settings.processor = processor
    settings.save()

    assert resolve_processor_name_for_import(processor_name) == processor.name


@pytest.mark.parametrize("processor_name", [None, "Unknown"])
def test_resolve_processor_name_rejects_unconfigured_default_despite_available_devices(
    processor_name: str | None,
) -> None:
    EndoscopyProcessor.objects.create(name="first_unrelated_processor")
    EndoscopyProcessor.objects.create(name="olympus_cv_1500")
    settings = ApplicationSettings.get_solo()
    settings.processor = cast(EndoscopyProcessor, None)
    settings.save()

    with pytest.raises(ValueError, match=r"ApplicationSettings\.processor"):
        resolve_processor_name_for_import(processor_name)


@pytest.mark.parametrize(
    "configured_name", ["unknown", "undefined", "none", "null", " "]
)
def test_resolve_processor_name_rejects_undefined_application_default(
    configured_name: str,
) -> None:
    processor = EndoscopyProcessor.objects.create(name=configured_name)
    settings = ApplicationSettings.get_solo()
    settings.processor = processor
    settings.save()

    with pytest.raises(ValueError, match=r"ApplicationSettings\.processor"):
        resolve_processor_name_for_import(None)


def test_resolve_processor_name_preserves_explicit_processor_name() -> None:
    assert (
        resolve_processor_name_for_import(" missing_processor ") == "missing_processor"
    )


def test_reimport_processor_name_prefers_video_processor_over_legacy_meta() -> None:
    video = SimpleNamespace(
        processor=SimpleNamespace(name="video_processor"),
        video_meta=SimpleNamespace(processor=SimpleNamespace(name="legacy_processor")),
    )

    assert _processor_name(cast(VideoFile, video)) == "video_processor"


def test_reimport_processor_name_uses_default_for_missing_processor_relations() -> None:
    processor = EndoscopyProcessor.objects.create(name="configured_reimport_processor")
    settings = ApplicationSettings.get_solo()
    settings.processor = processor
    settings.save()
    video = SimpleNamespace(processor=None, video_meta=SimpleNamespace(processor=None))

    assert _processor_name(cast(VideoFile, video)) == processor.name


def test_import_context_rejects_missing_processor_without_application_default() -> None:
    settings = ApplicationSettings.get_solo()
    settings.processor = cast(EndoscopyProcessor, None)
    settings.save()
    video_like = SimpleNamespace(
        center=SimpleNamespace(name="configured-center"),
        processor=None,
        video_meta=None,
        raw_video_hash="missing-processor-context",
    )

    with pytest.raises(ValueError, match=r"ApplicationSettings\.processor"):
        get_video_import_context_names(cast(VideoFile, video_like))
