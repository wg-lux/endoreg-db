"""Encrypted activation imports and explicit wall-clock to PTS bindings."""

from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING
import os
import re
from pathlib import Path

import pytest
import yaml
from cryptography.exceptions import InvalidTag
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db.models.deletion import ProtectedError
from lx_dtypes.models.contracts.video_provenance import (
    RecordingSynchronization,
    SynchronizationAnchor,
    VideoFrameProvenancePayload,
    VideoFrameTimeline,
)
from lx_dtypes.models.contracts.electrosurgical_activations import (
    ElectrosurgicalActivation,
)
from rest_framework.test import APIClient

from endoreg_db.models import Center, PortalUserInfo, VideoFile
from endoreg_db.models.media.video.electrosurgical_activation import (
    VideoElectrosurgicalActivationLog,
)
from endoreg_db.schemas.electrosurgical_activation import ActivationSourceFormat
from endoreg_db.services.electrical.activation_alignment import align_activation_export
from endoreg_db.services.electrical.activation_storage import (
    import_activation_export,
    load_activation_export,
)
from endoreg_db.services.electrical.activation_import import parse_activation_export
from endoreg_db.services.electrical.video_alignment import (
    ElectricalAlignmentUnavailable,
)
from endoreg_db.services.video_storage.frame_provenance import persist_frame_provenance
from endoreg_db.utils.encryption.encrypted import MAGIC, EncryptedStorage
from endoreg_db.utils.file_operations import atomic_write_file
from tests.services.test_electrosurgical_activation_import import workbook

pytestmark = pytest.mark.django_db
RAW = "a" * 64
GENERATION = "b" * 64
START = (
    int(
        (
            datetime(2026, 1, 1, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        ).total_seconds()
    )
    * 1_000_000
    + 123456
)
SYNC = RecordingSynchronization(
    session_id="1" * 32,
    video_clock_id="2" * 32,
    electrical_clock_id="3" * 32,
    source_content_hash=RAW,
    electrical_time_base_num=1,
    electrical_time_base_den=1_000_000,
    method="shared_hardware_event",
    anchors=[
        SynchronizationAnchor(video_timestamp=100, electrical_timestamp=START),
        SynchronizationAnchor(
            video_timestamp=2300, electrical_timestamp=START + 2_200_000
        ),
    ],
    uncertainty_num=1,
    uncertainty_den=1000,
)


@pytest.fixture
def csv_export() -> bytes:
    return yaml.safe_load(
        Path(__file__).with_name("electrical_activation_export.yml").read_text()
    )["csv"].encode()


@pytest.fixture
def video() -> VideoFile:
    persist_frame_provenance(
        VideoFrameProvenancePayload(
            source=VideoFrameTimeline(
                content_hash=RAW,
                time_base_num=1,
                time_base_den=1000,
                presentation_timestamps=[100, 200, 1100, 2100, 2300],
            ),
            output=VideoFrameTimeline(
                content_hash=GENERATION,
                time_base_num=1,
                time_base_den=1000,
                presentation_timestamps=[0, 100, 1000, 2000, 2200],
            ),
            source_frame_indices=[0, 1, 2, 3, 4],
        )
    )
    return VideoFile.objects.create(
        center=Center.objects.create(name="activation-test"),
        raw_video_hash=RAW,
        processed_video_hash=GENERATION,
        meta={},
    )


def import_log(
    video: VideoFile,
    content: bytes,
    kind: ActivationSourceFormat = ActivationSourceFormat.CSV,
):
    result = import_activation_export(
        video.pk,
        content=content,
        source_format=kind,
        source_timezone="Etc/GMT-1",
        synchronization=SYNC,
        max_timing_error_seconds=Decimal("0.01"),
    )
    video.refresh_from_db()
    return result


@pytest.mark.parametrize("kind", list(ActivationSourceFormat))
def test_encrypted_import_preserves_summaries_and_pts(
    video: VideoFile, csv_export: bytes, kind: ActivationSourceFormat
) -> None:
    content = csv_export if kind == ActivationSourceFormat.CSV else workbook(csv_export)
    result = import_log(video, content, kind)
    assert result.created
    stored = Path(result.activation_log.source_file.path).read_bytes()
    assert stored.startswith(MAGIC) and content not in stored
    assert len(load_activation_export(result.activation_log).log.activations) == 3
    again = import_log(video, content, kind)
    assert not again.created and again.activation_log.pk == result.activation_log.pk
    page = align_activation_export(
        video, result.activation_log, expected_generation_content_hash=GENERATION
    )
    # XLSX's third timestamp rounds one microsecond beyond the CSV timestamp.
    # Its end falls outside these exact anchors: do not extrapolate it.
    last_window = (2, 2.2) if kind == ActivationSourceFormat.CSV else (None, None)
    assert [(row.start_seconds, row.end_seconds) for row in page.activations] == [
        (0, 0.1),
        (1, 1),
        last_window,
    ]
    energies = [Decimal(".125"), Decimal(0)]
    if kind == ActivationSourceFormat.CSV:
        energies.append(Decimal(".5"))
    assert [row.summary.energy_j for row in page.activations if row.summary] == energies
    assert page.range.next_first_activation is None
    partial = align_activation_export(
        video,
        result.activation_log,
        expected_generation_content_hash=GENERATION,
        activation_count=1,
    )
    assert partial.range.next_first_activation == 1
    with pytest.raises(ElectricalAlignmentUnavailable):
        align_activation_export(
            video, result.activation_log, expected_generation_content_hash="c" * 64
        )


@pytest.mark.parametrize("fault", ["wrong_key", "tamper"])
def test_authenticated_reads_fail_closed(
    video: VideoFile,
    csv_export: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    field = VideoElectrosurgicalActivationLog._meta.get_field("source_file")
    monkeypatch.setattr(
        field, "storage", EncryptedStorage(location=tmp_path, master_key=b"k" * 32)
    )
    record = import_log(video, csv_export).activation_log
    path = Path(record.source_file.path)
    ciphertext = path.read_bytes()
    if fault == "wrong_key":
        record.source_file.storage = EncryptedStorage(
            location=tmp_path, master_key=b"z" * 32
        )
    else:
        atomic_write_file(
            destination=path, content=[ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])]
        )
    with pytest.raises((InvalidTag, ValueError, RuntimeError)):
        load_activation_export(record)
    assert [p for p in tmp_path.rglob("*") if p.is_file() and p.suffix != ".lock"] == [
        path
    ]
    assert all(
        re.fullmatch(rb"pid=[0-9]+ acquired_monotonic=[0-9]+\n", p.read_bytes())
        for p in tmp_path.rglob("*.lock")
    )
    assert path.read_bytes().startswith(MAGIC)


def test_failed_persistence_removes_source_and_binding(
    video: VideoFile, csv_export: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        VideoElectrosurgicalActivationLog._meta.get_field("source_file"),
        "storage",
        EncryptedStorage(location=tmp_path, master_key=b"k" * 32),
    )
    original_meta = video.meta

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(VideoElectrosurgicalActivationLog, "save", fail)
    with pytest.raises(RuntimeError, match="injected"):
        import_log(video, csv_export)
    video.refresh_from_db()
    assert video.meta == original_meta
    assert not VideoElectrosurgicalActivationLog.objects.exists()
    assert not [p for p in tmp_path.rglob("*") if p.is_file() and p.suffix != ".lock"]
    assert all(
        re.fullmatch(rb"pid=[0-9]+ acquired_monotonic=[0-9]+\n", p.read_bytes())
        for p in tmp_path.rglob("*.lock")
    )


def test_protected_import_prevents_media_deletion(
    video: VideoFile, csv_export: bytes
) -> None:
    video.raw_file.save("activation-test.mp4", ContentFile(b"synthetic video bytes"))
    record = import_log(video, csv_export).activation_log
    raw_name = str(video.raw_file.name)
    assert video.raw_file.storage.exists(raw_name)
    with pytest.raises(ProtectedError):
        video.delete()
    assert video.raw_file.storage.exists(raw_name)
    assert record.source_file.storage.exists(str(record.source_file.name))
    assert VideoFile.objects.filter(pk=video.pk).exists()


def test_api_requires_center_and_generation(
    video: VideoFile, csv_export: bytes
) -> None:
    record = import_log(video, csv_export).activation_log
    user = User.objects.create_user(username="activation-viewer")
    portal = PortalUserInfo.objects.create(user=user)
    portal.centers.add(video.center)
    client = APIClient()
    client.force_authenticate(user=user)
    url = f"/api/media/videos/{video.pk}/electrical-activations/{record.pk}/"
    query = {"generation_content_hash": GENERATION, "activation_count": 2}
    response = client.get(url, query)
    assert response.status_code == 200, response.content
    assert response.json()["range"]["next_first_activation"] == 2
    assert response.json()["activations"][0]["summary"]["energy_j"] == "0.125"
    assert client.get(url).status_code == 400
    assert client.get(url, {**query, "activation_count": 501}).status_code == 400
    assert (
        client.get(url, {**query, "generation_content_hash": "c" * 64}).status_code
        == 409
    )
    metadata = dict(record.metadata)
    metadata["normalized_sha256"] = "0" * 64
    VideoElectrosurgicalActivationLog.objects.filter(pk=record.pk).update(
        metadata=metadata
    )
    refused = client.get(url, query)
    assert refused.status_code == 409
    assert refused.json()["code"] == "source_integrity_failed"
    portal.centers.clear()
    assert client.get(url, query).status_code == 403
    assert APIClient().get(url, query).status_code in (401, 403)


@pytest.mark.parametrize("fault", ["timezone", "tolerance", "recording"])
def test_conflicting_bindings_do_not_change_import(
    video: VideoFile, csv_export: bytes, fault: str
) -> None:
    record = import_log(video, csv_export).activation_log
    assert video.meta is not None
    metadata, original_meta = dict(record.metadata), dict(video.meta)
    sync = (
        SYNC
        if fault != "recording"
        else SYNC.model_copy(update={"source_content_hash": "c" * 64})
    )
    with pytest.raises(ValueError):
        import_activation_export(
            video.pk,
            content=csv_export,
            source_format=ActivationSourceFormat.CSV,
            source_timezone="UTC" if fault == "timezone" else "Etc/GMT-1",
            synchronization=sync,
            max_timing_error_seconds=Decimal("0.0001")
            if fault == "tolerance"
            else Decimal("0.01"),
        )
    record.refresh_from_db()
    video.refresh_from_db()
    assert record.metadata == metadata and video.meta == original_meta
    assert VideoElectrosurgicalActivationLog.objects.count() == 1


@pytest.mark.parametrize("kind", list(ActivationSourceFormat))
def test_operator_supplied_exports(kind: ActivationSourceFormat) -> None:
    """Opt-in real export acceptance; anchors and video identities are synthetic.

    No supplied clinical bytes are checked into the repository or printed.
    The directory must be explicitly selected by the operator running pytest.
    """
    directory = os.environ.get("ACTIVATION_ACCEPTANCE_DIRECTORY")
    if not directory:
        pytest.skip("Set ACTIVATION_ACCEPTANCE_DIRECTORY to test supplied exports")
    content = (Path(directory) / f"Activations.{kind.value}").read_bytes()
    parsed = parse_activation_export(
        content, source_format=kind, source_timezone="Etc/GMT-1"
    )
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    starts: list[int] = []
    for activation in parsed.log.activations:
        delta = activation.started_at - epoch
        starts.append(
            (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        )
    origin = starts[0]
    boundaries = {tick - origin for tick in starts}
    for start, activation in zip(starts, parsed.log.activations, strict=True):
        duration_us = int(
            (activation.duration_s * 1_000_000).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        boundaries.add(start - origin + duration_us)
    ticks = sorted(boundaries | {max(boundaries) + 1_000_000})
    raw, generation = "d" * 64, "f" * 64
    persist_frame_provenance(
        VideoFrameProvenancePayload(
            source=VideoFrameTimeline(
                content_hash=raw,
                time_base_num=1,
                time_base_den=1_000_000,
                presentation_timestamps=ticks,
            ),
            output=VideoFrameTimeline(
                content_hash=generation,
                time_base_num=1,
                time_base_den=1_000_000,
                presentation_timestamps=ticks,
            ),
            source_frame_indices=list(range(len(ticks))),
        )
    )
    video = VideoFile.objects.create(
        center=Center.objects.create(name="supplied-activation-test"),
        raw_video_hash=raw,
        processed_video_hash=generation,
        meta={},
    )
    sync = SYNC.model_copy(
        update={
            "source_content_hash": raw,
            "anchors": [
                SynchronizationAnchor(video_timestamp=0, electrical_timestamp=origin),
                SynchronizationAnchor(
                    video_timestamp=ticks[-1], electrical_timestamp=origin + ticks[-1]
                ),
            ],
        }
    )
    result = import_activation_export(
        video.pk,
        content=content,
        source_format=kind,
        source_timezone="Etc/GMT-1",
        synchronization=sync,
        max_timing_error_seconds=Decimal("0.01"),
    )
    assert result.created
    video.refresh_from_db()
    assert load_activation_export(result.activation_log) == parsed
    assert Path(result.activation_log.source_file.path).read_bytes().startswith(MAGIC)
    first = 0
    mapped: list[ElectrosurgicalActivation | None] = []
    while True:
        page = align_activation_export(
            video,
            result.activation_log,
            expected_generation_content_hash=generation,
            first_activation=first,
            activation_count=100,
        )
        assert all(row.status == "mapped" for row in page.activations)
        mapped.extend(row.summary for row in page.activations)
        if page.range.next_first_activation is None:
            break
        first = page.range.next_first_activation
    assert mapped == parsed.log.activations
    assert len(mapped) == 868  # Expected row count of the two supplied exports.
