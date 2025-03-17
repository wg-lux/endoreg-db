import shutil
import subprocess
from pathlib import Path
from django.db import models
from typing import TYPE_CHECKING, List, Tuple

from icecream import ic
from tqdm import tqdm
import cv2
from django.core.validators import FileExtensionValidator
from django.core.files.storage import FileSystemStorage

from endoreg_db.utils.validate_endo_roi import validate_endo_roi
from ..base_classes.utils import (
    anonymize_frame,
    RAW_VIDEO_DIR_NAME,
    VIDEO_DIR,
    STORAGE_LOCATION,
)
from ..base_classes.abstract_video import AbstractVideoFile

if TYPE_CHECKING:
    # import Queryset
    from django.db.models import QuerySet
    from endoreg_db.models import (
        SensitiveMeta,
        LabelVideoSegment,
    )


# pylint: disable=attribute-defined-outside-init,no-member
class RawVideoFile(AbstractVideoFile):
    """ """

    file = models.FileField(
        upload_to=RAW_VIDEO_DIR_NAME,
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],  # FIXME
        storage=FileSystemStorage(location=STORAGE_LOCATION.resolve().as_posix()),
    )

    patient = models.ForeignKey(
        "Patient", on_delete=models.SET_NULL, blank=True, null=True
    )

    sensitive_meta = models.ForeignKey(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        related_name="raw_videos",
        null=True,
        blank=True,
    )

    video = models.ForeignKey(
        "Video",
        on_delete=models.SET_NULL,
        related_name="raw_videos",
        null=True,
        blank=True,
    )

    if TYPE_CHECKING:
        sensitive_meta: "SensitiveMeta"
        label_video_segments: "QuerySet[LabelVideoSegment]"

    # Crop Frames
    state_anonymized_frames_generated = models.BooleanField(default=False)

    ## OCR
    state_ocr_required = models.BooleanField(default=True)
    state_ocr_completed = models.BooleanField(default=False)
    ## Validation
    state_outside_validated = models.BooleanField(default=False)
    state_ocr_result_validated = models.BooleanField(default=False)

    state_sensitive_data_retrieved = models.BooleanField(default=False)

    # Censor Outside
    state_censor_outside_required = models.BooleanField(default=True)
    state_censor_outside_completed = models.BooleanField(default=False)
    state_make_anonymized_video_required = models.BooleanField(default=True)
    state_make_anonymized_video_completed = models.BooleanField(default=False)

    def get_anonymized_video_path(self):
        video_dir = VIDEO_DIR
        video_suffix = Path(self.file.path).suffix
        video_name = f"{self.uuid}{video_suffix}"
        anonymized_video_name = f"TMP_anonymized_{video_name}"
        anonymized_video_path = video_dir / anonymized_video_name

        return anonymized_video_path

    def censor_outside_frames(self):
        assert self.state_frames_extracted, "Frames not extracted"
        assert self.state_initial_prediction_completed, (
            "Initial prediction not completed"
        )
        assert self.state_sensitive_data_retrieved, "Sensitive data not retrieved"

        ic(
            "WARNING: Outside validation is not yet implemented and automatically set to true in this function"
        )

        self.state_outside_validated = True
        self.save()

        assert self.state_outside_validated, "Outside validation not completed"

        outside_frame_paths = self.get_outside_frame_paths()

        if not outside_frame_paths:
            ic("No outside frames found")

        else:
            ic(f"Found {len(outside_frame_paths)} outside frames")
            # use cv2 to replace all outside frames with completely black frames

            for frame_path in tqdm(outside_frame_paths):
                frame = cv2.imread(frame_path.as_posix())
                frame.fill(0)
                cv2.imwrite(frame_path.as_posix(), frame)

        self.state_censor_outside_required = False
        self.state_censor_outside_completed = True
        self.save()

    def get_anonymized_frame_dir(self):
        anonymized_frame_dir = Path(self.frame_dir).parent / f"tmp_{self.uuid}"
        return anonymized_frame_dir

    def make_temporary_anonymized_frames(self) -> Tuple[Path, List[Path]]:
        anonymized_frame_dir = self.get_anonymized_frame_dir()

        assert self.state_frames_extracted, "Frames not extracted"
        assert self.processor, "Processor not set"

        anonymized_frame_dir.mkdir(parents=True, exist_ok=True)
        endo_roi = self.get_endo_roi()
        assert validate_endo_roi(endo_roi), "Endoscope ROI is not valid"
        generated_frame_paths = []

        all_frames = self.frames.all()
        outside_frames = self.get_outside_frames()  #
        outside_frame_numbers = [frame.frame_number for frame in outside_frames]

        # anonymize frames: copy endo-roi content while making other pixels black. (frames are Path objects to jpgs or pngs)
        for frame in tqdm(all_frames):
            frame_path = Path(frame.image.path)
            frame_name = frame_path.name
            frame_number = frame.frame_number

            if frame_number in outside_frame_numbers:
                all_black = True
            else:
                all_black = False

            target_frame_path = anonymized_frame_dir / frame_name
            anonymize_frame(
                frame_path, target_frame_path, endo_roi, all_black=all_black
            )
            generated_frame_paths.append(target_frame_path)

        return anonymized_frame_dir, generated_frame_paths

    def make_anonymized_video(self):
        """
        Make an anonymized video from the anonymized frames.
        """

        assert self.state_initial_prediction_completed, (
            "Initial prediction not completed"
        )
        assert self.state_sensitive_data_retrieved, "Sensitive data not retrieved"

        ic(
            "WARNING: Outside validation is not yet implemented and automatically set to true in this function"
        )
        self.state_outside_validated = True
        self.save()

        assert self.state_outside_validated, "Outside validation not completed"

        _anonymized_frame_dir, generated_frame_paths = (
            self.make_temporary_anonymized_frames()
        )

        anonymized_video_path = self.get_anonymized_video_path()
        # if anonymized video already exists, delete it
        if anonymized_video_path.exists():
            anonymized_video_path.unlink()

        # Use ffmpeg and the frame paths to create a video
        fps = self.get_fps()
        height, width = cv2.imread(generated_frame_paths[0].as_posix()).shape[:2]
        ic("Assembling anonymized video")
        ic(f"Frame width: {width}, height: {height}")
        ic(f"FPS: {fps}")

        command = [
            "ffmpeg",
            "-y",
            "-pattern_type",
            "glob",
            "-f",
            "image2",
            "-framerate",
            str(fps),
            "-i",
            f"{generated_frame_paths[0].parent.as_posix()}/frame_[0-9]*.jpg",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            f"scale={width}:{height}",
            anonymized_video_path.as_posix(),
        ]

        subprocess.run(command, check=True)
        ic(f"Anonymized video saved at {anonymized_video_path}")

        self.state_make_anonymized_video_required = False
        self.state_make_anonymized_video_completed = True
        self.save()

        return anonymized_video_path, generated_frame_paths

    def delete_frames_anonymized(self):
        """
        Delete anonymized frames extracted from the video file.
        """
        frame_dir = Path(self.frame_dir)
        anonymized_frame_dir = frame_dir.parent / f"anonymized_{self.uuid}"
        if anonymized_frame_dir.exists():
            shutil.rmtree(anonymized_frame_dir)
            return f"Anonymized frames deleted from {anonymized_frame_dir}"
        else:
            return f"No anonymized frames to delete for {self.file.name}"

    def get_or_create_video(self):
        from endoreg_db.models import Video, Patient, PatientExamination

        video = self.video
        expected_path = self.get_anonymized_video_path()
        if not video:
            video_hash = self.video_hash
            if Video.objects.filter(video_hash=video_hash).exists():
                video = Video.objects.filter(video_hash=video_hash).first()

            else:
                if not expected_path.exists():
                    ic(
                        f"No anonymized video found at {expected_path}, Creating new one"
                    )
                    video_path, frame_paths = self.make_anonymized_video()

                else:
                    ic(f"Anonymized video found at {expected_path}")
                    video_path = expected_path
                    frame_dir = self.get_anonymized_frame_dir()
                    ic(f"Frame dir: {frame_dir}")
                    frame_paths = list(frame_dir.glob("*.jpg"))
                    ic(f"Found {len(frame_paths)} frames")

                video_object = Video.create_from_file(
                    video_path,
                    self.center,
                    self.processor,
                    video_dir=VIDEO_DIR,
                    frame_paths=frame_paths,
                )

                ex: PatientExamination = self.sensitive_meta.pseudo_examination
                pat: Patient = self.sensitive_meta.pseudo_patient
                video_object.examination = ex
                video_object.patient = pat

                self.video = video_object
                self.save()
                video_object.sync_from_raw_video()

                ic(f"Video object created: {video_object}")
                return video_object

            self.video = video
            self.save()

        # self.vi
        return video
