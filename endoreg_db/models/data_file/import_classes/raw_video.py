import shutil
import subprocess
from pathlib import Path
from django.db import models

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


# pylint: disable=attribute-defined-outside-init,no-member
class RawVideoFile(AbstractVideoFile):
    """
    RawVideoFile is a Django model representing a raw video file with associated metadata and processing states.
    Attributes:
        uuid (UUIDField): Unique identifier for the video file.
        file (FileField): The video file, restricted to .mp4 format.
        sensitive_meta (OneToOneField): Metadata containing sensitive information.
        center (ForeignKey): The center associated with the video.
        processor (ForeignKey): The processor associated with the video.
        video_meta (OneToOneField): Metadata containing video-specific information.
        original_file_name (CharField): The original name of the video file.
        video_hash (CharField): Unique hash of the video file.
        uploaded_at (DateTimeField): Timestamp of when the video was uploaded.
        state_frames_required (BooleanField): Indicates if frame extraction is required.
        state_frames_extracted (BooleanField): Indicates if frames have been extracted.
        state_initial_prediction_required (BooleanField): Indicates if initial prediction is required.
        state_initial_prediction_completed (BooleanField): Indicates if initial prediction is completed.
        state_initial_prediction_import_required (BooleanField): Indicates if initial prediction import is required.
        state_initial_prediction_import_completed (BooleanField): Indicates if initial prediction import is completed.
        state_ocr_required (BooleanField): Indicates if OCR is required.
        state_ocr_completed (BooleanField): Indicates if OCR is completed.
        state_outside_validated (BooleanField): Indicates if outside validation is completed.
        state_ocr_result_validated (BooleanField): Indicates if OCR result validation is completed.
        state_sensitive_data_retrieved (BooleanField): Indicates if sensitive data has been retrieved.
        state_histology_required (BooleanField): Indicates if histology data is required.
        state_histology_available (BooleanField): Indicates if histology data is available.
        state_follow_up_intervention_required (BooleanField): Indicates if follow-up intervention is required.
        state_follow_up_intervention_available (BooleanField): Indicates if follow-up intervention data is available.
        state_dataset_complete (BooleanField): Indicates if the dataset is complete.
        state_anonymized_frames_generated (BooleanField): Indicates if anonymized frames have been generated.
        state_anonym_video_required (BooleanField): Indicates if anonymized video is required.
        state_anonym_video_performed (BooleanField): Indicates if anonymized video has been performed.
        state_original_reports_deleted (BooleanField): Indicates if original reports have been deleted.
        state_original_video_deleted (BooleanField): Indicates if the original video has been deleted.
        state_finalized (BooleanField): Indicates if the video processing is finalized.
        frame_dir (CharField): Directory where frames are stored.
        prediction_dir (CharField): Directory where predictions are stored.
    Methods:
        transcode_videofile(filepath, transcoded_path): Transcodes a video to MP4 format using ffmpeg.
        create_from_file(file_path, center_name, processor_name, ...): Creates a RawVideoFile instance from a given video file.
        predict_video(model_meta_name, dataset_name, ...): Predicts the video file using the given model.
        get_anonymized_frame_dir(): Generates the path to the anonymized frame directory.
        check_anonymized_frames_exist(): Checks if anonymized frames exist for the video file.
        generate_anonymized_frames(): Generates anonymized frames from the video file.
        delete_with_file(): Deletes the video file along with its frames and anonymized frames.
        get_endo_roi(): Fetches the endoscope ROI from the video meta.
        get_crop_template(): Creates a crop template from the endoscope ROI.
        set_frame_dir(): Sets the frame directory based on the UUID.
        save(*args, **kwargs): Saves the RawVideoFile instance to the database.
        extract_frames(quality, frame_dir, ...): Extracts frames from the video file using ffmpeg.
        delete_frames(): Deletes frames extracted from the video file.
        delete_frames_anonymized(): Deletes anonymized frames extracted from the video file.
        get_frame_path(n, anonymized): Gets the path to the n-th frame extracted from the video file.
        get_frame_paths(anonymized): Gets paths to all frames extracted from the video file.
        get_prediction_dir(): Gets the directory where predictions are stored.
        get_predictions_path(suffix): Gets the path to the predictions file.
        get_smooth_predictions_path(suffix): Gets the path to the smooth predictions file.
        get_binary_predictions_path(suffix): Gets the path to the binary predictions file.
        get_raw_sequences_path(suffix): Gets the path to the raw sequences file.
        get_filtered_sequences_path(suffix): Gets the path to the filtered sequences file.
        extract_text_information(frame_fraction): Extracts text information from the video file.
        update_text_metadata(ocr_frame_fraction): Updates the text metadata for the video file.
        update_video_meta(): Updates the video metadata.
        get_fps(): Gets the frames per second (FPS) of the video file.
    """

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

    def get_anonymized_frame_dir(self):
        """Method to generate the path to the anonymized frame directory"""
        return Path(self.frame_dir).parent / f"anonymized_{self.uuid}"

    def check_anonymized_frames_exist(self):
        """
        Check if anonymized frames exist for the video file.
        """
        frame_dir = Path(self.frame_dir)
        anonymized_frame_dir = self.get_anonymized_frame_dir()
        anonymized_frame_dir.mkdir(parents=True, exist_ok=True)
        n_frames = len(list(frame_dir.glob("*")))
        if len(list(anonymized_frame_dir.glob("*"))) == n_frames:
            ic(f"Anonymized frames already generated for {self.file.name}")
            frames_already_extracted = True
        else:
            ic(f"Anonymized frames not generated for {self.file.name}")
            frames_already_extracted = False
            # make sure directory is empty
            for f in anonymized_frame_dir.glob("*"):
                f.unlink()

        self.state_anonymized_frames_generated = frames_already_extracted
        self.save()

        return frames_already_extracted

    def generate_anonymized_frames(self):
        """
        Generate anonymized frames from the video file.
        """

        if not self.state_frames_extracted:
            ic(f"Frames not extracted for {self.file.name}")
            return None

        anonymized_frames_already_extracted = self.check_anonymized_frames_exist()
        anonymized_frame_dir = self.get_anonymized_frame_dir()
        self.state_anonymized_frames_generated = anonymized_frames_already_extracted

        endo_roi = self.get_endo_roi()
        assert validate_endo_roi(endo_roi), "Endoscope ROI is not valid"

        if not self.state_anonymized_frames_generated:
            # anonymize frames: copy endo-roi content while making other pixels black. (frames are Path objects to jpgs or pngs)
            for frame_path in tqdm(self.get_frame_paths()):
                frame_name = frame_path.name
                target_frame_path = anonymized_frame_dir / frame_name
                anonymize_frame(frame_path, target_frame_path, endo_roi)

        self.state_anonymized_frames_generated = True
        self.save()

        return f"Anonymized frames at {anonymized_frame_dir}"

    def censor_outside_frames(self):
        assert self.state_frames_extracted, "Frames not extracted"
        assert self.state_anonymized_frames_generated, "Anonymized frames not generated"
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

    def get_anonymized_video_path(self):
        video_dir = VIDEO_DIR
        video_name = Path(self.file.path).name
        anonymized_video_name = f"TMP_anonymized_{video_name}"
        anonymized_video_path = video_dir / anonymized_video_name

        return anonymized_video_path

    def make_anonymized_video(self):
        """
        Make an anonymized video from the anonymized frames.
        """
        from endoreg_db.models import Video

        assert self.state_frames_extracted, "Frames not extracted"
        assert self.state_anonymized_frames_generated, "Anonymized frames not generated"
        assert self.state_initial_prediction_completed, (
            "Initial prediction not completed"
        )
        assert self.state_sensitive_data_retrieved, "Sensitive data not retrieved"
        assert self.state_outside_validated, "Outside validation not completed"
        assert self.state_censor_outside_completed, (
            "Censoring outside frames not completed"
        )

        frame_paths = self.get_frame_paths(anonymized=True)

        anonymized_video_path = self.get_anonymized_video_path()
        # if anonymized video already exists, delete it
        if anonymized_video_path.exists():
            anonymized_video_path.unlink()

        # Use ffmpeg and the frame paths to create a video
        fps = self.get_fps()
        height, width = cv2.imread(frame_paths[0].as_posix()).shape[:2]
        ic("Assembling anonymized video")
        ic(f"Frame width: {width}, height: {height}")
        ic(f"FPS: {fps}")

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "image2",
            "-framerate",
            str(fps),
            "-i",
            f"{frame_paths[0].parent.as_posix()}/frame_%07d.jpg",  # changed from '*.jpg'
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

        # # Create Video File Object

        self.state_make_anonymized_video_required = False
        self.state_make_anonymized_video_completed = True
        self.save()

        return anonymized_video_path

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
        from warnings import warn

        video = self.video
        expected_path = self.get_anonymized_video_path()
        if not video:
            video_hash = self.video_hash
            if Video.objects.filter(video_hash=video_hash).exists():
                video = Video.objects.filter(video_hash=video_hash).first()

            else:
                if not expected_path.exists():
                    video_path = self.make_anonymized_video()
                else:
                    video_path = expected_path
                video_object = Video.create_from_file(
                    video_path,
                    self.center,
                    self.processor,
                    video_dir=VIDEO_DIR,
                    delete_source=False,
                    delete_temporary_transcoded_file=True,
                    save=True,
                )

                ex: PatientExamination = self.sensitive_meta.pseudo_examination
                pat: Patient = self.sensitive_meta.pseudo_patient
                video_object.examination = ex
                video_object.pseudo_patient = pat

                self.video = video_object
                self.save()
                video_object.sync_from_raw_video()

                ic(f"Video object created: {video_object}")
                return video_object

            self.video = video
            self.save()

        # self.vi
        return video
