from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from lx_dtypes.models.contracts.video_masks import VideoMaskProfile
from pydantic import ValidationError

from endoreg_db.services import video_masks
from endoreg_db.services.jobs.video_correction_jobs import (
    apply_video_anonymization_strategy,
)
from endoreg_db.schemas.video_jobs import VideoAnonymizationCorrectionJobConfig
from endoreg_db.views.video.correction import VideoAnonymizationCorrectionView


pytestmark = pytest.mark.no_db


@pytest.fixture
def mask_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        video_masks, "get_runtime_paths", lambda: SimpleNamespace(terminology=tmp_path)
    )
    return tmp_path


def profile() -> VideoMaskProfile:
    return VideoMaskProfile.model_validate(
        {
            "name": "room_2",
            "label": "Room 2",
            "device_name": "Test device",
            "mask": {
                "x": 20,
                "y": 10,
                "width": 100,
                "height": 80,
                "image_width": 160,
                "image_height": 120,
            },
        }
    )


def test_create_load_select_and_apply_snapshot(mask_root: Path) -> None:
    original = profile()
    assert video_masks.create_video_mask(original) == original
    assert video_masks.get_video_mask(original.name) == original
    payload = VideoAnonymizationCorrectionView.validate_payload(
        {
            "strategy": "processor_region",
            "human_review_required": True,
            "region": {"mode": "profile", "mask_name": original.name},
        }
    )
    config = VideoAnonymizationCorrectionJobConfig.model_validate(
        {**payload, "queue": "ffmpeg"}
    )
    # Persisted snapshot remains usable without the catalogue file.
    (mask_root / "video_masks" / "room_2.yml").unlink()
    cleaner = MagicMock()
    cleaner.mask_application.mask_video_streaming.return_value = True
    apply_video_anonymization_strategy(
        frame_cleaner=cleaner,
        raw_path=mask_root / "raw.mp4",
        output_path=mask_root / "candidate.mp4",
        config=config,
    )
    assert (
        cleaner.mask_application.mask_video_streaming.call_args.kwargs["mask_config"]
        == original.mask.model_dump()
    )
    cleaner.mask_application._load_mask.assert_not_called()


def test_duplicates_and_unknown_names_rejected(mask_root: Path) -> None:
    video_masks.create_video_mask(profile())
    with pytest.raises(FileExistsError):
        video_masks.create_video_mask(profile())
    with pytest.raises(ValueError, match="Unknown"):
        video_masks.get_video_mask("../../outside")
    assert len(video_masks.list_video_masks()) >= 2


@pytest.mark.parametrize(
    "field,value", [("width", 0), ("height", -1), ("x", 150), ("image_width", 0)]
)
def test_invalid_coordinates_rejected(field: str, value: int) -> None:
    mask = profile().mask.model_dump()
    mask[field] = value
    with pytest.raises(ValidationError):
        VideoMaskProfile.model_validate(
            {
                "name": "room",
                "label": "Room",
                "device_name": "Test device",
                "mask": mask,
            }
        )
