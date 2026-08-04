from __future__ import annotations

from types import SimpleNamespace

import pytest

from endoreg_db.models import ApplicationSettings, EndoscopyProcessor
from endoreg_db.services.jobs.video_reimport_jobs import _processor_name
from endoreg_db.services.video_files.processor_resolution import (
    DEFAULT_PROCESSOR_FALLBACK_NAME,
    resolve_processor_name_for_import,
)

pytestmark = pytest.mark.django_db


def test_resolve_processor_name_uses_application_default_for_unknown():
    processor = EndoscopyProcessor.objects.create(name="configured_default_processor")
    settings = ApplicationSettings.get_solo()
    settings.processor = processor
    settings.save()

    assert resolve_processor_name_for_import("Unknown") == processor.name
    assert resolve_processor_name_for_import(None) == processor.name


def test_resolve_processor_name_uses_named_fallback_when_no_application_default():
    processor = EndoscopyProcessor.objects.create(name=DEFAULT_PROCESSOR_FALLBACK_NAME)
    settings = ApplicationSettings.get_solo()
    settings.processor = None
    settings.save()

    assert resolve_processor_name_for_import("Unknown") == processor.name


def test_resolve_processor_name_preserves_explicit_processor_name():
    assert resolve_processor_name_for_import("missing_processor") == "missing_processor"


def test_reimport_processor_name_prefers_video_processor_over_legacy_meta():
    video = SimpleNamespace(
        processor=SimpleNamespace(name="video_processor"),
        video_meta=SimpleNamespace(processor=SimpleNamespace(name="legacy_processor")),
    )

    assert _processor_name(video) == "video_processor"


def test_reimport_processor_name_uses_default_for_missing_processor_relations():
    processor = EndoscopyProcessor.objects.create(name=DEFAULT_PROCESSOR_FALLBACK_NAME)
    settings = ApplicationSettings.get_solo()
    settings.processor = None
    settings.save()
    video = SimpleNamespace(processor=None, video_meta=SimpleNamespace(processor=None))

    assert _processor_name(video) == processor.name
