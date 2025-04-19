import shutil
from pathlib import Path
from django.db import models
from typing import TYPE_CHECKING, List, Tuple
import subprocess

from django.core.validators import FileExtensionValidator
from django.core.files.storage import FileSystemStorage
from endoreg_db.utils.validate_endo_roi import validate_endo_roi
from ..base_classes.abstract_video import AbstractVideoFile
from ...utils import _assemble_anonymized_video, _create_anonymized_frame_files, _censor_outside_frames, ANONYM_VIDEO_DIR, STORAGE_DIR, _get_anonymized_video_path


# pylint: disable=attribute-defined-outside-init,no-member
class RawVideoFile(AbstractVideoFile):
    """
    Represents a raw video file uploaded to the system.

    This model handles the raw video data, potentially containing sensitive information,
    before it undergoes anonymization. It links to sensitive metadata and provides
    methods for processing, anonymizing, and managing the video file and its
    associated frames.

    Attributes:
        file (FileField): The raw video file itself. Stored in a designated
                          directory before processing. Currently validates for 'mp4'. # FIXME
        patient (ForeignKey): Link to the associated Patient model (can be null).
        sensitive_meta (ForeignKey): Link to the SensitiveMeta model containing
                                     potentially identifying information related to the video.
        video (ForeignKey): Link to the anonymized Video model, created after processing.
    """
    file = models.FileField(
        upload_to=str(ANONYM_VIDEO_DIR.relative_to(STORAGE_DIR)),
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],  # FIXME: Review allowed extensions
        storage=FileSystemStorage(location=STORAGE_DIR.as_posix()),
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
        from endoreg_db.models import (SensitiveMeta, LabelVideoSegment, Video)
        sensitive_meta: "SensitiveMeta"
        label_video_segments: "models.QuerySet[LabelVideoSegment]"
        video: "Video"

    def get_anonymized_video_path(self) -> Path:
        """
        Constructs and returns the expected path for the anonymized version of this video.

        Returns:
            Path: The absolute path where the anonymized video file should be stored.
        """
        anonymized_video_path = _get_anonymized_video_path(raw_video=self)
        return anonymized_video_path

    def censor_outside_frames(self):
        """
        Applies censoring (e.g., blurring or blacking out) to frames identified
        as being outside the region of interest (endoscopic view).

        This modifies the frame files directly based on associated LabelVideoSegment data.
        """
        self.save() # Ensure any pending changes are saved before processing
        assert _censor_outside_frames(raw_video=self), "Failed to censor outside frames"
        self.save() # Save potential changes made during censoring (if model state is affected)

    def get_anonymized_frame_dir(self) -> Path:
        """
        Determines the temporary directory path for storing anonymized frames.

        Returns:
            Path: The path to the temporary directory for anonymized frames.
        """
        anonymized_frame_dir = Path(self.frame_dir).parent / f"tmp_{self.uuid}"
        return anonymized_frame_dir

    def make_temporary_anonymized_frames(self) -> Tuple[Path, List[Path]]:
        """
        Generates anonymized versions of the video frames in a temporary directory.

        Extracts frames, applies the endoscope ROI mask, and censors outside frames.

        Returns:
            Tuple[Path, List[Path]]: A tuple containing:
                - The path to the temporary directory holding the anonymized frames.
                - A list of paths to the generated anonymized frame image files.

        Raises:
            AssertionError: If the processor is not set or the endoscope ROI is invalid.
        """
        assert self.processor, "Processor not set"
        anonymized_frame_dir = self.get_anonymized_frame_dir()
        anonymized_frame_dir.mkdir(parents=True, exist_ok=True)

        endo_roi = self.get_endo_roi()
        assert validate_endo_roi(endo_roi_dict=endo_roi), "Endoscope ROI is not valid"

        all_frames = self.frames.all()
        outside_frames = self.get_outside_frames()
        outside_frame_numbers = [frame.frame_number for frame in outside_frames]

        generated_frame_paths = _create_anonymized_frame_files(
            anonymized_frame_dir=anonymized_frame_dir,
            endo_roi=endo_roi,
            frames=all_frames,
            outside_frame_numbers=outside_frame_numbers,
        )
        return anonymized_frame_dir, generated_frame_paths

    def make_anonymized_video(self) -> Tuple[Path, List[Path]]:
        """
        Creates the final anonymized video file from the temporary anonymized frames.

        First generates the temporary anonymized frames, then assembles them into
        a video file at the designated anonymized video path. Deletes any
        pre-existing anonymized video file.

        Returns:
            Tuple[Path, List[Path]]: A tuple containing:
                - The path to the newly created anonymized video file.
                - A list of paths to the temporary anonymized frame files used.

        Raises:
            AssertionError: If video assembly fails.
        """
        _anonymized_frame_dir, generated_frame_paths = (
            self.make_temporary_anonymized_frames()
        )

        anonymized_video_path = self.get_anonymized_video_path()
        # if anonymized video already exists, delete it
        if anonymized_video_path.exists():
            anonymized_video_path.unlink()

        fps = self.get_fps()
        try:
            _assemble_anonymized_video(
                generated_frame_paths=generated_frame_paths,
                anonymized_video_path=anonymized_video_path,
                fps=fps,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Failed to assemble anonymized video") from exc
        # Note: Consider deleting the temporary anonymized frames directory here
        # or ensuring it's cleaned up elsewhere.
        # Example: shutil.rmtree(_anonymized_frame_dir)

        return anonymized_video_path, generated_frame_paths

    def maintenance(self):
        """
        Placeholder for potential maintenance tasks related to the raw video file.
        (e.g., cleanup, consistency checks). Currently not implemented.
        """
        pass # TODO: Implement maintenance logic if needed

    def _delete_frames_anonymized(self) -> bool:
        """
        Deletes the temporary directory containing anonymized frames, if it exists.

        Returns:
            bool: True if the directory was successfully deleted or didn't exist,
                  False otherwise (though shutil.rmtree raises exceptions on failure).
        """
        anonymized_frame_dir = self.get_anonymized_frame_dir()

        if anonymized_frame_dir.exists():
            shutil.rmtree(anonymized_frame_dir)
            # Consider adding error handling for rmtree if necessary

        return True

    def _anonym_video_file_exists(self) -> bool:
        """
        Checks if the physical anonymized video file exists on the filesystem.

        Returns:
            bool: True if the file exists, False otherwise.
        """
        anonymized_video_path = self.get_anonymized_video_path()
        return anonymized_video_path.exists()

    def _anonym_video_object_exists(self) -> bool:
        """
        Checks if this RawVideoFile instance is linked to an anonymized Video object
        in the database.

        Returns:
            bool: True if the 'video' foreign key is set, False otherwise.
        """
        return self.video is not None

    def get_or_create_video(self, sync: bool = False):
        """
        Retrieves the associated anonymized Video object or creates it if it doesn't exist.

        Ensures the physical anonymized video file exists, creating it if necessary.
        If the Video object doesn't exist in the database, it creates one using
        the anonymized video file and frame data.

        Args:
            sync (bool): If True, syncs metadata (like patient, examination) from
                         the sensitive metadata associated with this raw video to the
                         newly created or existing anonymized Video object and links
                         this RawVideoFile to the Video object. Defaults to False.

        Returns:
            Video: The existing or newly created anonymized Video object.
        """
        from endoreg_db.models import Video #, Patient, PatientExamination - Removed unused imports

        video_hash = self.video_hash # Assuming video_hash is inherited or defined
        anonym_video_object_exists = self._anonym_video_object_exists()
        anonym_video_file_exists = self._anonym_video_file_exists()
        anonym_video_path = self.get_anonymized_video_path()

        if not anonym_video_file_exists:
            # Note: make_anonymized_video returns paths, but we only need the side effect here.
            anonym_video_path, _ = self.make_anonymized_video()
            # Consider adding cleanup for generated_frame_paths here if not done in make_anonymized_video
            # self._delete_frames_anonymized()

        video = None
        if anonym_video_object_exists:
            # If the link exists, trust it. Alternatively, could query by hash.
            video = self.video
            # Optional: Verify consistency if needed
            # video = Video.objects.filter(video_hash=video_hash).first()
            # assert video == self.video, "Mismatch between linked video and hash lookup"

        if video is None: # If not linked or link was None
            # Attempt to find existing video by hash before creating a new one
            video = Video.objects.filter(video_hash=video_hash).first()

            if video is None: # If still not found, create it
                anonym_frame_dir = self.get_anonymized_frame_dir()
                # Ensure frames exist if video file was just created or existed before
                if not anonym_frame_dir.exists():
                     _, _ = self.make_temporary_anonymized_frames() # Regenerate if missing

                anonym_frame_paths = list(anonym_frame_dir.glob("*.jpg"))
                if not anonym_frame_paths:
                    # Handle case where frame generation might have failed silently
                    # or directory is empty unexpectedly
                    raise FileNotFoundError(f"Anonymized frames not found in {anonym_frame_dir}")


                video = Video.create_from_file(
                    file_path=anonym_video_path,
                    center_name=self.center.name, # Assuming center is inherited/defined
                    processor_name=self.processor.name, # Assuming processor is inherited/defined
                    video_dir=ANONYM_VIDEO_DIR,
                    frame_paths=anonym_frame_paths,
                    # Pass hash explicitly if create_from_file doesn't calculate it
                    # video_hash=video_hash
                )
                # Optional cleanup after successful creation
                # self._delete_frames_anonymized()


        if sync:
            # TODO: Make sure to sync all required information here
            # FIXME: Review and complete the synchronization logic
            if self.sensitive_meta:
                video.examination = self.sensitive_meta.pseudo_examination
                video.patient = self.sensitive_meta.pseudo_patient
            # Assuming sync_from_raw_video exists on Video model
            video.sync_from_raw_video(self) # Pass self for more context if needed
            video.save() # Save changes to the Video object

            self.video = video # Link this RawVideoFile to the Video object
            self.save() # Save the link

        return video
