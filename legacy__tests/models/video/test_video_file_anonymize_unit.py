from pathlib import Path

import numpy as np
import pytest

import endoreg_db.models.media.video.video_file_anonymize as anonymize_module
from endoreg_db.models import Center, Frame, VideoFile


@pytest.mark.django_db
def test_create_anonymized_frame_files_masks_outside_frames(tmp_path, monkeypatch):
    center = Center.objects.create(name="mask-center", display_name="Mask Center")
    video = VideoFile.objects.create(center=center, video_hash="hash-mask")

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video.frame_dir = str(frame_dir)
    video.save(update_fields=["frame_dir"])

    frame_specs = [
        (0, 80),
        (1, 160),
    ]
    for frame_number, intensity in frame_specs:
        relative_name = f"frame_{frame_number:07d}.jpg"
        path = frame_dir / relative_name
        image = np.full((4, 4, 3), intensity, dtype=np.uint8)
        anonymize_module.cv2.imwrite(path.as_posix(), image)
        Frame.objects.create(
            video=video,
            frame_number=frame_number,
            relative_path=relative_name,
            is_extracted=True,
        )

    anonymized_dir = tmp_path / "anonymized"
    anonymized_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(anonymize_module, "tqdm", lambda iterable, **_: iterable)

    endo_roi = {"x": 0, "y": 0, "width": 4, "height": 4}
    outside_numbers = {1}

    generated = anonymize_module._create_anonymized_frame_files(
        video=video,
        anonymized_frame_dir=anonymized_dir,
        endo_roi=endo_roi,
        frames=video.frames.all(),
        outside_frame_numbers=outside_numbers,
        censor_color=(5, 5, 5),
    )

    assert len(generated) == len(frame_specs)
    assert all(path.parent == anonymized_dir for path in generated)

    inside_image = anonymize_module.cv2.imread((anonymized_dir / "frame_0000000.jpg").as_posix())
    outside_image = anonymize_module.cv2.imread((anonymized_dir / "frame_0000001.jpg").as_posix())

    assert inside_image is not None and inside_image.mean() > 0
    assert outside_image is not None and np.all(outside_image == 5)
