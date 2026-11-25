import json
import tempfile
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from django.core.management import call_command
from django.test import TransactionTestCase

from endoreg_db.models import AIDataSet, Center, Frame, LabelSet, VideoFile
from endoreg_db.utils.file_operations import safe_rmtree
from endoreg_db.utils.paths import EndoregPathsModel


class LegacyImageRow(TypedDict):
    filename: str
    old_examination_id: int
    labels: list[str]


def _legacy_row_filename(row: LegacyImageRow) -> str:
    return row["filename"]


def _model_pk(value: object) -> int:
    pk = getattr(value, "pk", None)
    assert pk is not None
    return int(pk)


def _frame_video_id(frame: Frame) -> int:
    video_id = getattr(frame, "video_id", None)
    assert video_id is not None
    return int(video_id)


class LegacyLoadCommandBackfillTest(TransactionTestCase):
    def test_old_examination_id_rows_are_backfilled_to_video_ids(self) -> None:
        unique = uuid4().hex
        center = Center.objects.create(name=f"legacy-backfill-center-{unique}")
        labelset = LabelSet.objects.create(
            name=f"legacy-backfill-labelset-{unique}",
            version=1,
        )
        staged_root = (
            EndoregPathsModel.from_environment().storage
            / "migration_staging"
            / "legacy_data"
            / f"test_backfill_{unique}"
        )
        manifest_path = staged_root / "manifest.json"

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                images_root = tmp_path / "images"
                images_root.mkdir()
                rows: list[LegacyImageRow] = [
                    {
                        "filename": "exam-101-a.jpg",
                        "old_examination_id": 101,
                        "labels": ["legacy-polyp"],
                    },
                    {
                        "filename": "exam-101-b.jpg",
                        "old_examination_id": 101,
                        "labels": ["legacy-polyp"],
                    },
                    {
                        "filename": "exam-202-a.jpg",
                        "old_examination_id": 202,
                        "labels": ["legacy-normal"],
                    },
                ]
                for row in rows:
                    (images_root / _legacy_row_filename(row)).write_bytes(
                        b"legacy-image"
                    )

                jsonl_path = tmp_path / "legacy.jsonl"
                jsonl_path.write_text(
                    "\n".join(json.dumps(row) for row in rows),
                    encoding="utf-8",
                )

                call_command(
                    "load_legacy_data",
                    jsonl_path=str(jsonl_path),
                    images_root=str(images_root),
                    center_id=_model_pk(center),
                    dataset_name=f"legacy-backfill-dataset-{unique}",
                    labelset_name=labelset.name,
                    labelset_version=labelset.version,
                    staged_images_root=str(staged_root),
                    manifest_path=str(manifest_path),
                    verbosity=0,
                )

            frames_by_filename = {
                frame.relative_path: frame
                for frame in Frame.objects.select_related("video").all()
            }
            first_video_id = _frame_video_id(frames_by_filename["exam-101-a.jpg"])
            second_video_id = _frame_video_id(frames_by_filename["exam-101-b.jpg"])
            other_video_id = _frame_video_id(frames_by_filename["exam-202-a.jpg"])

            assert len(frames_by_filename) == 3
            assert first_video_id == second_video_id
            assert first_video_id != other_video_id
            assert [
                frame.frame_number
                for frame in Frame.objects.filter(video_id=first_video_id).order_by(
                    "frame_number"
                )
            ] == [1, 2]
            assert [
                frame.frame_number
                for frame in Frame.objects.filter(video_id=other_video_id).order_by(
                    "frame_number"
                )
            ] == [1]
            assert (
                VideoFile.objects.filter(
                    raw_video_hash__startswith=f"legacy_exam_c{_model_pk(center)}_"
                ).count()
                == 2
            )
            assert (
                AIDataSet.objects.get(
                    name=f"legacy-backfill-dataset-{unique}"
                ).image_annotations.count()
                == 3
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert manifest["fallback_video_id"] is None
            assert manifest["center_id"] == _model_pk(center)
            assert manifest["legacy_video_ids_by_old_examination_id"] == {
                "101": first_video_id,
                "202": other_video_id,
            }
        finally:
            safe_rmtree(staged_root, missing_ok=True)
