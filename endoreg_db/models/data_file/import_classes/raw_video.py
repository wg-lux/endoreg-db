from django.db import models
from pathlib import Path
from collections import defaultdict, Counter

from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.file_operations import get_uuid_filename
from endoreg_db.utils.ocr import extract_text_from_rois

import shutil
import os
import subprocess

from ..metadata import VideoMeta, SensitiveMeta

class RawVideoFile(models.Model):
    uuid = models.UUIDField()
    file = models.FileField(upload_to="raw_data/")
    
    sensitive_meta = models.OneToOneField(
        "SensitiveMeta", on_delete=models.CASCADE, blank=True, null=True
    ) 

    center = models.ForeignKey("Center", on_delete=models.CASCADE)
    processor = models.ForeignKey(
        "EndoscopyProcessor", on_delete=models.CASCADE, blank=True, null=True
    )
    video_meta = models.OneToOneField(
        "VideoMeta", on_delete=models.CASCADE, blank=True, null=True
    )
    original_file_name = models.CharField(max_length=255)
    video_hash = models.CharField(max_length=255, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Frame Extraction States
    state_frames_required = models.BooleanField(default=True)
    state_frames_extracted = models.BooleanField(default=False)

    # Video
    ## Prediction
    state_initial_prediction_required = models.BooleanField(default=True)
    state_initial_prediction_completed = models.BooleanField(default=False)
    state_initial_prediction_import_required = models.BooleanField(default=True)
    state_initial_prediction_import_completed = models.BooleanField(default=False)
    ## OCR
    state_ocr_required = models.BooleanField(default=True)
    state_ocr_completed = models.BooleanField(default=False)
    ## Validation
    state_outside_validated = models.BooleanField(default=False)
    state_ocr_result_validated = models.BooleanField(default=False)

    state_sensitive_data_retrieved = models.BooleanField(default=False)

    # Dataset complete?
    state_histology_required = models.BooleanField(blank=True, null=True)
    state_histology_available = models.BooleanField(default=False)
    state_follow_up_intervention_required = models.BooleanField(blank=True, null=True)
    state_follow_up_intervention_available = models.BooleanField(default=False)
    state_dataset_complete = models.BooleanField(default=False)

    # Finalizing for Upload
    state_anonym_video_required = models.BooleanField(default=True)
    state_anonym_video_performed = models.BooleanField(default=False)
    state_original_reports_deleted = models.BooleanField(default=False)
    state_original_video_deleted = models.BooleanField(default=False)
    state_finalized = models.BooleanField(default=False)

    frame_dir = models.CharField(max_length=255)
    prediction_dir = models.CharField(max_length=255)

    @classmethod
    def create_from_file(
        cls,
        file_path: Path,
        video_dir: Path,
        center_name: str,
        processor_name: str,
        frame_dir_parent: Path,
        save: bool = True,
    ):
        from endoreg_db.models import Center, EndoscopyProcessor

        print(f"Creating RawVideoFile from {file_path}")
        original_file_name = file_path.name
        # Rename and and move

        new_file_name, uuid = get_uuid_filename(file_path)
        framedir: Path = frame_dir_parent / str(uuid)

        if not framedir.exists():
            framedir.mkdir(parents=True, exist_ok=True)

        if not video_dir.exists():
            video_dir.mkdir(parents=True, exist_ok=True)

        video_hash = get_video_hash(file_path)

        center = Center.objects.get(name=center_name)
        assert center is not None, "Center must exist"

        processor = EndoscopyProcessor.objects.get(name=processor_name)
        assert processor is not None, "Processor must exist"

        new_filepath = video_dir / new_file_name

        print(f"Moving {file_path} to {new_filepath}")
        shutil.move(file_path.resolve().as_posix(), new_filepath.resolve().as_posix())
        print(f"Moved to {new_filepath}")

        # Make sure file was transferred correctly and hash is correct
        if not new_filepath.exists():
            print(f"File {file_path} was not transferred correctly to {new_filepath}")
            return None

        new_hash = get_video_hash(new_filepath)
        if new_hash != video_hash:
            print(f"Hash of file {file_path} is not correct")
            return None

        # make sure that no other file with the same hash exists
        if cls.objects.filter(video_hash=video_hash).exists():
            # log and print warnint
            print(f"File with hash {video_hash} already exists")
            return None

        else:
            print(center)
            # Create a new instance of RawVideoFile
            raw_video_file = cls(
                uuid=uuid,
                file=new_filepath.resolve().as_posix(),
                center=center,
                processor=processor,
                original_file_name=original_file_name,
                video_hash=video_hash,
                frame_dir=framedir.as_posix(),
            )

            # Save the instance to the database
            raw_video_file.save()

            return raw_video_file

    def __str__(self):
        return self.file.name

    def get_endo_roi(self):
        endo_roi = self.video_meta.get_endo_roi()
        return endo_roi

    # video meta should be created when video file is created
    def save(self, *args, **kwargs):
        if self.video_meta is None:
            center = self.center
            processor = self.processor
            self.video_meta = VideoMeta.objects.create(
                center=center, processor=processor
            )
            self.video_meta.initialize_ffmpeg_meta(self.file.path)
        super(RawVideoFile, self).save(*args, **kwargs)

    def extract_frames(
        self,
        quality: int = 2,
        frame_dir: Path = None,
        overwrite: bool = False,
        ext="jpg",
    ):
        """
        Extract frames from the video file and save them to the frame_dir.
        For this, ffmpeg must be available in in the current environment.
        """
        if frame_dir is None:
            frame_dir = Path(self.frame_dir)
        else:
            frame_dir = Path(frame_dir)

        if not frame_dir.exists():
            frame_dir.mkdir(parents=True, exist_ok=True)

        if not overwrite and len(list(frame_dir.glob("*.jpg"))) > 0:
            print(f"Frames already extracted for {self.file.name}")
            return

        video_path = Path(self.file.path).resolve().as_posix()

        frame_path_string = frame_dir.resolve().as_posix()
        command = [
            "ffmpeg",
            "-i",
            video_path,  #
            "-q:v",
            str(quality),
            os.path.join(frame_path_string, f"frame_%07d.{ext}"),
        ]

        # Ensure FFmpeg is available
        if not shutil.which("ffmpeg"):
            raise EnvironmentError(
                "FFmpeg could not be found. Ensure it is installed and in your PATH."
            )

        # Extract frames from the video file
        # Execute the command
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Error extracting frames: {result.stderr}")

        self.state_frames_extracted = True

        return f"Frames extracted to {frame_dir} ({frame_path_string}) with quality {quality}"

    def delete_frames(self):
        """
        Delete frames extracted from the video file.
        """
        frame_dir = Path(self.frame_dir)
        if frame_dir.exists():
            shutil.rmtree(frame_dir)
            self.state_frames_extracted = False
            self.save()
            return f"Frames deleted from {frame_dir}"
        else:
            return f"No frames to delete for {self.file.name}"

    def get_frame_path(self, n: int = 0):
        """
        Get the path to the n-th frame extracted from the video file.
        Note that the frame numbering starts at 1 in our naming convention.
        """
        # Adjust index
        n = n + 1

        frame_dir = Path(self.frame_dir)
        return frame_dir / f"frame_{n:07d}.jpg"
    
    def get_frame_paths(self):
        if not self.state_frames_extracted:
            return None
        frame_dir = Path(self.frame_dir)
        paths = [p for p in frame_dir.glob('*')]
        indices = [int(p.stem.split("_")[1]) for p in paths]
        path_index_tuples = list(zip(paths, indices))
        # sort ascending by index
        path_index_tuples.sort(key=lambda x: x[1])
        paths, indices = zip(*path_index_tuples)

        return paths

    def get_prediction_dir(self):
        return Path(self.prediction_dir)

    def get_predictions_path(self, suffix = ".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("predictions").with_suffix(suffix)
    
    def get_smooth_predictions_path(self, suffix = ".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("smooth_predictions").with_suffix(suffix)
    
    def get_binary_predictions_path(self, suffix = ".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("binary_predictions").with_suffix(suffix)
    
    def get_raw_sequences_path(self, suffix = ".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("raw_sequences").with_suffix(suffix)

    def get_filtered_sequences_path(self, suffix=".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("filtered_sequences").with_suffix(suffix)

    def extract_text_information(self, frame_fraction: float = 0.001):
        """
        Extract text information from the video file.
        Makes sure that frames are extracted and then processes the frames.
        gets all frames from frame_dir and selects a fraction of them to process (at least 1)
        """
        if not self.state_frames_extracted:
            print(f"Frames not extracted for {self.file.name}")
            return None

        processor = self.processor

        frame_dir = Path(self.frame_dir)
        frames = list(frame_dir.glob("*"))
        n_frames = len(frames)
        n_frames_to_process = max(1, int(frame_fraction * n_frames))

        # Select evenly spaced frames
        frames = frames[:: n_frames // n_frames_to_process]

        # extract text from each frame and store the value to
        # defaultdict of lists.
        # Then, extract the most frequent value from each list
        # Finally, return the dictionary of most frequent values

        # Create a defaultdict to store the extracted text from each ROI
        rois_texts = defaultdict(list)

        print(f"Processing {n_frames_to_process} frames from {self.file.name}")
        # Process frames
        for frame_path in frames[:n_frames_to_process]:
            extracted_texts = extract_text_from_rois(frame_path, processor)
            for roi, text in extracted_texts.items():
                rois_texts[roi].append(text)

        # Get the most frequent text values for each ROI using Counter
        for key in rois_texts.keys():
            counter = Counter([text for text in rois_texts[key] if text])
            rois_texts[key] = counter.most_common(1)[0][0] if counter else None

        return rois_texts

    def update_text_metadata(self, ocr_frame_fraction=0.001):
        print(f"Updating metadata for {self.file.name}")
        texts = self.extract_text_information(ocr_frame_fraction)

        self.sensitive_meta = SensitiveMeta.create_from_dict(texts)
        self.state_sensitive_data_retrieved = True
        self.save()

        # Resulting dict depends on defined ROIs for this processor type!

    def update_video_meta(self):
        video_meta = self.video_meta
        video_path = Path(self.file.path)

        if video_meta is None:
            video_meta = VideoMeta.create_from_video(video_path)
            self.video_meta = video_meta
            self.save()

        else:
            video_meta.update_meta(video_path)

    def get_fps(self):
        if self.video_meta is None:
            self.update_video_meta()

        if self.video_meta.ffmpeg_meta is None:
            self.video_meta.initialize_ffmpeg_meta(self.file.path)

        return self.video_meta.get_fps()
