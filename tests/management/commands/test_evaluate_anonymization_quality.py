from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from io import StringIO
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone
import pytest

from endoreg_db.models import (
    AnonymizationValidationMetric,
    Center,
    SensitiveMeta,
    VideoFile,
)


@pytest.fixture(autouse=True)
def _reference_data(base_db_data):
    return base_db_data


@pytest.mark.django_db
def test_quality_command_json_output_is_derived_only_and_snake_case(tmp_path):
    center = Center.objects.create(name=f"quality-command-center-{uuid4().hex[:8]}")
    sensitive_meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="CommandAlice",
        patient_last_name="CommandPatient",
        patient_dob=timezone.make_aware(datetime(1992, 7, 6)),
        examination_date=date(2026, 2, 1),
        casenumber="COMMAND-CASE-17",
        anonymized_text="The report still says CommandAlice.",
    )
    video = VideoFile.objects.create(
        center=center,
        sensitive_meta=sensitive_meta,
        video_hash=f"quality-command-video-{uuid4().hex}",
    )
    video.processed_file.save("processed.mp4", ContentFile(b"processed"), save=True)
    video.get_or_create_state().mark_anonymization_validated()
    AnonymizationValidationMetric.objects.create(
        media_type="video",
        video=video,
        sensitive_meta=sensitive_meta,
        center=center,
    )

    output_path = tmp_path / "quality.json"
    stdout = StringIO()
    today = timezone.localdate()
    call_command(
        "evaluate_anonymization_quality",
        "--video-id",
        str(video.pk),
        "--date-from",
        (today - timedelta(days=1)).isoformat(),
        "--date-to",
        (today + timedelta(days=1)).isoformat(),
        "--json-output",
        str(output_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == file_payload
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["residual_phi_detected_count"] == 1
    assert payload["summary"]["missing_sensitive_meta_deletion_count"] > 0
    assert payload["results"][0]["checked_fields"]

    payload_text = json.dumps(payload)
    assert "CommandAlice" not in payload_text
    assert "CommandPatient" not in payload_text
    assert "COMMAND-CASE-17" not in payload_text
    _assert_snake_case_keys(payload)


@pytest.mark.django_db
def test_quality_command_apply_policy_clears_direct_identifiers():
    center = Center.objects.create(name=f"quality-apply-center-{uuid4().hex[:8]}")
    sensitive_meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="ApplyAlice",
        patient_last_name="ApplyPatient",
        patient_dob=timezone.make_aware(datetime(1993, 1, 2)),
        examination_date=date(2026, 2, 2),
        casenumber="APPLY-CASE-17",
    )
    video = VideoFile.objects.create(
        center=center,
        sensitive_meta=sensitive_meta,
        video_hash=f"quality-apply-video-{uuid4().hex}",
    )
    video.processed_file.save("processed.mp4", ContentFile(b"processed"), save=True)
    video.get_or_create_state().mark_anonymization_validated()
    AnonymizationValidationMetric.objects.create(
        media_type="video",
        video=video,
        sensitive_meta=sensitive_meta,
        center=center,
    )

    call_command(
        "evaluate_anonymization_quality",
        "--video-id",
        str(video.pk),
        "--apply-policy",
        stdout=StringIO(),
    )

    sensitive_meta.refresh_from_db()
    assert sensitive_meta.patient_first_name is None
    assert sensitive_meta.patient_last_name is None
    assert sensitive_meta.patient_dob is None
    assert sensitive_meta.casenumber is None
    assert sensitive_meta.patient_hash


def _assert_snake_case_keys(value):
    import re

    snake_case = re.compile(r"^[a-z][a-z0-9_]*$")
    if isinstance(value, dict):
        for key, child in value.items():
            assert snake_case.match(key), key
            _assert_snake_case_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_snake_case_keys(child)
