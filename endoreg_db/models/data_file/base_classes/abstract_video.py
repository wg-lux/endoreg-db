"""
This module provides an abstract base class for video file processing in a Django application.
The AbstractVideoFile class encapsulates the core functionality needed to handle video files, \
    including:

• Transcoding videos using FFmpeg to ensure compatibility, with support for \
    copying files directly if already in MP4 format.
• Creating and managing video database entries by computing unique video hashes, \
    handling file storage, and avoiding duplicate entries.
• Extracting video frames (raw and anonymized) into designated directories for further processing.
• Running AI-based predictions on extracted frames using preconfigured models \
    and generating comprehensive prediction outputs:
    - Raw predictions, readable predictions, merged predictions, smooth merged predictions, \
        and binarized smooth merged predictions.
    - Detection of sequences from binary predictions.
• Extracting textual information from video frames using OCR to update associated metadata.
• Providing utility methods to handle various aspects of file and directory management, \
    such as cleaning up frames and updating video metadata.
• Integrating with other modules that manage video-specific settings, \
    including cropping based on region-of-interest (ROI) parameters,
  center and processor associations, and performance controls (e.g., frames per second).

Designed to be inherited and extended, this module lays the groundwork for \
    building a robust video processing pipeline,
particularly in domains like endoscopy, where accurate video analysis and \
    metadata extraction are critical.
"""

from pathlib import Path
from collections import defaultdict, Counter
import shutil
import os
import subprocess
from typing import Optional, List
from django.db import models
from icecream import ic

from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.file_operations import get_uuid_filename
from endoreg_db.utils.ocr import extract_text_from_rois

from ..metadata import VideoMeta, SensitiveMeta
from .utils import (
    copy_with_progress,
    STORAGE_LOCATION,
    FRAME_DIR,
    get_transcoded_file_path,
)


class AbstractVideoFile(models.Model):
    """
    Abstract base class for video files.
    """

    uuid = models.UUIDField()

    sensitive_meta = models.OneToOneField(
        "SensitiveMeta", on_delete=models.CASCADE, blank=True, null=True
    )

    center = models.ForeignKey("Center", on_delete=models.CASCADE)
    processor = models.ForeignKey(  # TODO Migrate to VideoMeta
        "EndoscopyProcessor", on_delete=models.CASCADE, blank=True, null=True
    )
    # TODO Reduce redundancies between VideoFile and VideoMeta (e.g. center, processor)

    video_meta = models.OneToOneField(
        "VideoMeta", on_delete=models.CASCADE, blank=True, null=True
    )
    original_file_name = models.CharField(max_length=255)
    video_hash = models.CharField(max_length=255, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    frame_dir = models.CharField(max_length=255)
    prediction_dir = models.CharField(max_length=255)
    predictions = models.JSONField(default=dict)

    readable_predictions = models.JSONField(default=dict)
    merged_predictions = models.JSONField(default=dict)
    smooth_merged_predictions = models.JSONField(default=dict)
    binary_smooth_merged_predictions = models.JSONField(default=dict)
    sequences = models.JSONField(default=dict)

    # Frame Extraction States
    state_frames_required = models.BooleanField(default=True)
    state_frames_extracted = models.BooleanField(default=False)

    # Video
    ## Prediction
    state_initial_prediction_required = models.BooleanField(default=True)
    state_initial_prediction_completed = models.BooleanField(default=False)
    state_initial_prediction_import_required = models.BooleanField(default=True)
    state_initial_prediction_import_completed = models.BooleanField(default=False)

    # Dataset complete?
    state_histology_required = models.BooleanField(blank=True, null=True)
    state_histology_available = models.BooleanField(default=False)
    state_follow_up_intervention_required = models.BooleanField(blank=True, null=True)
    state_follow_up_intervention_available = models.BooleanField(default=False)
    state_dataset_complete = models.BooleanField(default=False)

    class Meta:
        abstract = True  #

    @classmethod
    def transcode_videofile(cls, filepath: Path, transcoded_path: Path):
        """
        Transcodes a video to a compatible MP4 format using ffmpeg.
        If the transcoded file exists, it is returned.

        Parameters
        ----------
        mov_file : str
            The full path to the video file.

        Returns
        -------
        transcoded_path : str
            The full path to the transcoded video file.
        """
        ic("Transcoding video")
        ic(f"Input path: {filepath}")

        # if filepath suffix is .mp4 or .MP4 we dont need to transcode and can copy the file
        if filepath.suffix.lower() in [".mp4"]:
            shutil.copyfile(filepath, transcoded_path)
            return transcoded_path

        ic(f"Transcoded path: {transcoded_path}")
        if os.path.exists(transcoded_path):
            return transcoded_path

        # Run ffmpeg to transcode the video using H264 and AAC
        # TODO Document settings, check if we need to change them
        command = [
            "ffmpeg",
            "-i",
            filepath.resolve().as_posix(),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            transcoded_path,
        ]
        subprocess.run(command, check=True)
        return transcoded_path

    @classmethod
    def check_hash_exists(cls, video_hash: str):
        return cls.objects.filter(video_hash=video_hash).exists()

    @classmethod
    def create_from_file(  # TODO Rename to get_or_create_from_file
        cls,
        file_path: Path,
        center_name: str,
        processor_name: str,
        frame_dir_parent: Path = Path("erc_data/raw_frames"),
        video_dir: Path = Path("erc_data/raw_videos"),
        delete_source: bool = False,
        delete_temporary_transcoded_file: bool = False,
        save: bool = True,
    ):
        """
        Creates a RawVideoFile instance from a given video file.
        Args:
            cls: The class itself.
            file_path (Path): The path to the video file.
            center_name (str): The name of the center associated with the video.
            processor_name (str): The name of the processor associated with the video.
            frame_dir_parent (Path, optional): The parent directory for frame storage. \
                Defaults to Path("erc_data/raw_frames").
            video_dir (Path, optional): The directory for video storage. \
                Defaults to Path("erc_data/raw_videos").
            delete_source (bool, optional): Whether to delete the source file after processing. \
                Defaults to False.
            save (bool, optional): Whether to save the instance to the database. Defaults to True.
        Returns:
            RawVideoFile: The created RawVideoFile instance, or an existing instance \
                if a file with the same hash already exists.
        """

        from endoreg_db.models import Center, EndoscopyProcessor  # pylint: disable=import-outside-toplevel

        ic(f"Creating {cls} from {file_path}")
        original_file_name = file_path.name

        # transcoded_filepath
        transcoded_file_path = get_transcoded_file_path(file_path, suffix="mp4")
        transcoded_file_name = f"{file_path.stem}_transcoded.mp4"

        if not transcoded_file_path.exists():
            ic("Transcoding video")
            ic(f"Input path: {file_path}")
            # Determine transcoded output filepath (appending '_transcoded.mp4')
            transcoded_file_path = file_path.parent / transcoded_file_name

            ic(f"Transcoded path: {transcoded_file_path}")
            if not os.path.exists(transcoded_file_path):
                cls.transcode_videofile(file_path, transcoded_path=transcoded_file_path)

            #

        video_hash = get_video_hash(transcoded_file_path)

        vid_with_hash_exists = cls.check_hash_exists(video_hash=video_hash)  # pylint: disable=no-member
        if vid_with_hash_exists:
            existing = cls.objects.get(video_hash=video_hash)
            ic(f"Existing DB entry found: {existing}")
            file_on_disk = STORAGE_LOCATION / existing.file.name
            if not file_on_disk.exists():
                ic("Existing DB entry found, but file is missing on disk. Copying...")
                raise Exception("File on disk is missing")
            ic("Returning existing entry")
            return existing

        new_file_name, uuid = get_uuid_filename(file_path)
        ic("No existing DB entry found")
        ic("New file name: ", new_file_name)
        ic("UUID: ", uuid)

        framedir: Path = frame_dir_parent / str(uuid)
        ic("Frame dir: ", framedir)

        if not framedir.exists():
            ic("Creating frame dir root")
            framedir.mkdir(parents=True, exist_ok=True)

        if not video_dir.exists():
            ic("Creating video dir root")
            video_dir.mkdir(parents=True, exist_ok=True)

        # make sure that no other file with the same hash exists

        center = Center.objects.get(name=center_name)
        assert center is not None, "Center must exist"

        processor = EndoscopyProcessor.objects.get(name=processor_name)
        assert processor is not None, "Processor must exist"

        new_filepath = video_dir / new_file_name

        ic(f"Copy {file_path} to {new_filepath}")
        copy_with_progress(
            transcoded_file_path.resolve().as_posix(), new_filepath.resolve().as_posix()
        )

        # cleanup transcoded temporary file
        if delete_temporary_transcoded_file:
            transcoded_file_path.unlink()

        # Make sure file was transferred correctly and hash is correct
        if not new_filepath.exists():
            ic(f"File {file_path} was not transferred correctly to {new_filepath}")
            return None

        new_hash = get_video_hash(new_filepath)
        if new_hash != video_hash:
            ic(f"Hash of file {file_path} is not correct")
            return None

        try:
            relative_path = new_filepath.relative_to(STORAGE_LOCATION)
        except ValueError:
            raise Exception(
                f"{new_filepath} is outside STORAGE_LOCATION {STORAGE_LOCATION}"
            )

        video = cls(
            uuid=uuid,
            file=relative_path.as_posix(),
            center=center,
            processor=processor,
            original_file_name=original_file_name,
            video_hash=video_hash,
            frame_dir=framedir.as_posix(),
        )

        # Save the instance to the database
        video.save()
        ic(f"Saved {video}")

        if delete_source:
            ic(f"Deleting source file {file_path}")
            file_path.unlink()

        return video

    def predict_video(
        self,
        model_meta_name: str,
        dataset_name: str = "inference_dataset",
        model_meta_version: Optional[int] = None,
        smooth_window_size_s: int = 1,
        binarize_threshold: float = 0.5,
        anonymized_frames: bool = True,
        img_suffix: str = ".jpg",
        test_run: bool = False,
        n_test_frames: int = 100,
    ):
        """
        Predict the video file using the given model.
        Frames should be extracted and anonymized frames should be generated before prediction.
        """
        from endoreg_db.models import ModelMeta, AiModel  # pylint: disable=import-outside-toplevel
        from endo_ai.predictor.inference_dataset import InferenceDataset  # pylint: disable=import-outside-toplevel
        from endo_ai.predictor.model_loader import MultiLabelClassificationNet
        from endo_ai.predictor.predict import Classifier
        from endo_ai.predictor.postprocess import (
            concat_pred_dicts,
            make_smooth_preds,
            find_true_pred_sequences,
        )

        datasets = {
            "inference_dataset": InferenceDataset,
        }

        dataset_model_class = datasets[dataset_name]

        if anonymized_frames:
            frame_dir = self.get_anonymized_frame_dir()

            assert self.check_anonymized_frames_exist(), "Anonymized frames not found"
            assert self.state_anonymized_frames_generated, (
                "State 'state_anonymized_frames_generated' must be True"
            )

        else:
            frame_dir = Path(self.frame_dir)
            assert self.state_frames_extracted, "Frames not extracted"

        model_meta = ModelMeta.get_by_name(model_meta_name, model_meta_version)
        model: AiModel = model_meta.model

        model_type = model.model_type
        model_subtype = model.model_subtype

        ic(f"Model type: {model_type}, Model subtype: {model_subtype}")

        paths = [p for p in frame_dir.glob(f"*{img_suffix}")]
        ic(f"Found {len(paths)} images in {frame_dir}")

        # frame names in format "frame_{index}.jpg"
        indices = [int(p.stem.split("_")[1]) for p in paths]
        path_index_tuples = list(zip(paths, indices))
        # sort ascending by index
        path_index_tuples.sort(key=lambda x: x[1])
        paths, indices = zip(*path_index_tuples)

        crop_template = self.get_crop_template()

        string_paths = [p.resolve().as_posix() for p in paths]
        crops = [crop_template for _ in paths]

        ic(f"Detected {len(paths)} frames")

        if test_run:  # only use the first 10 frames
            ic(f"Running in test mode, using only the first {n_test_frames} frames")
            paths = paths[:n_test_frames]
            indices = indices[:n_test_frames]
            string_paths = string_paths[:n_test_frames]
            crops = crops[:n_test_frames]

        assert paths, f"No images found in {frame_dir}"

        ds_config = model_meta.get_inference_dataset_config()

        # Create dataset
        ds = dataset_model_class(string_paths, crops, config=ds_config)
        ic(f"Dataset length: {len(ds)}")

        # Get a sample image
        sample = ds[0]
        ic("Shape:", sample.shape)  # e.g., torch.Size([3, 716, 716])

        # unorm = get_unorm(ds_config)

        weights_path = model_meta.weights.path

        ic(f"Model path: {weights_path}")

        # FIXME implement support for different model types
        ai_model_instance = MultiLabelClassificationNet.load_from_checkpoint(
            checkpoint_path=weights_path,
        )

        _ = ai_model_instance.cuda()
        _ = ai_model_instance.eval()
        classifier = Classifier(ai_model_instance, verbose=True)

        ic("Starting inference")
        predictions = classifier.pipe(string_paths, crops)

        ic("Creating Prediction Dict")
        prediction_dict = classifier.get_prediction_dict(predictions, string_paths)
        self.predictions = prediction_dict

        ic("Creating Readable Predictions")
        readable_predictions = [classifier.readable(p) for p in predictions]
        self.readable_predictions = readable_predictions

        ic("Creating Merged Predictions")
        merged_predictions = concat_pred_dicts(readable_predictions)

        fps = self.get_fps()
        ic(
            f"Creating Smooth Merged Predictions; FPS: {fps}, \
                Smooth Window Size: {smooth_window_size_s}"
        )

        smooth_merged_predictions = {}
        for key in merged_predictions.keys():
            smooth_merged_predictions[key] = make_smooth_preds(
                prediction_array=merged_predictions[key],
                window_size_s=smooth_window_size_s,
                fps=fps,
            )

        ic(
            "Creating Binary Smooth Merged Predictions; Binarize Threshold: ",
            binarize_threshold,
        )
        binary_smooth_merged_predictions = {}
        for key in smooth_merged_predictions.keys():  # pylint: disable=consider-using-dict-items
            binary_smooth_merged_predictions[key] = (
                smooth_merged_predictions[key] > binarize_threshold
            )

        ic("Creating Sequences")
        sequences = {}
        for label, prediction_array in binary_smooth_merged_predictions.items():
            sequences[label] = find_true_pred_sequences(prediction_array)

        self.sequences = sequences

        ic("Finished inference")
        ic("Saving predictions to DB")
        ic(sequences)
        self.state_initial_prediction_required = False
        self.state_initial_prediction_completed = True
        self.save()

    def get_outside_sequences(self, outside_label_name: str = "outside"):
        """
        Get sequences of outside frames.
        """
        from endoreg_db.models import VideoSegmentationLabel  # pylint: disable=import-outside-toplevel

        outside_label = VideoSegmentationLabel.objects.get(name=outside_label_name)
        assert outside_label is not None, "Outside label must exist"

        ic(f"Getting outside sequences using label: {outside_label}")

        outside_sequences = self.sequences.get(outside_label_name, [])

        if not outside_sequences:
            ic(f"No outside sequences found for {outside_label_name}")

        return outside_sequences

    def get_outside_frame_paths(self):
        """
        Get paths to outside frames.
        """
        outside_sequences = self.get_outside_sequences()
        frame_paths = self.get_frame_paths(anonymized=True)
        outside_frame_paths = []
        for start, stop in outside_sequences:
            outside_frame_paths.extend(frame_paths[start:stop])

        return outside_frame_paths

    def __str__(self):
        return self.file.name

    def delete_with_file(self):
        file_path = Path(self.file.path)
        if file_path.exists():
            file_path.unlink()
        self.delete_frames()
        self.delete_frames_anonymized()
        self.delete()
        return f"Deleted {self.file.name}; Deleted frames; Deleted anonymized frames"

    def get_endo_roi(self):
        """
        Fetches the endoscope ROI from the video meta.
        Returns a dictionary with keys "x", "y", "width", "height"
        """
        endo_roi = self.video_meta.get_endo_roi()
        return endo_roi

    def get_crop_template(self):
        """
        Creates a crop template (e.g., [0, 1080, 550, 1920 - 20] for a 1080p frame) from the endoscope ROI.
        """
        endo_roi = self.get_endo_roi()
        x = endo_roi["x"]
        y = endo_roi["y"]
        width = endo_roi["width"]
        height = endo_roi["height"]

        crop_template = [y, y + height, x, x + width]
        return crop_template

    def set_frame_dir(self):
        self.frame_dir = f"{FRAME_DIR}/{self.uuid}"

    # video meta should be created when video file is created
    def save(self, *args, **kwargs):
        if self.video_meta is None:
            center = self.center
            processor = self.processor
            self.video_meta = VideoMeta.objects.create(
                center=center, processor=processor
            )
            self.video_meta.initialize_ffmpeg_meta(self.file.path)

        if not self.frame_dir:
            self.set_frame_dir()

        super(AbstractVideoFile, self).save(*args, **kwargs)

    def extract_frames(
        self,
        quality: int = 2,
        frame_dir: Path = None,
        overwrite: bool = False,
        ext="jpg",
        verbose=False,
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

        if not overwrite and len(list(frame_dir.glob(f"*.{ext}"))) > 0:
            print(f"Frames already extracted for {self.file.name}")
            self.state_frames_extracted = True  # Mark frames as extracted
            return f"Frames already extracted at {frame_dir}"

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
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        stdout_data, stderr_data = process.communicate()

        if process.returncode != 0:
            raise Exception(f"Error extracting frames: {stderr_data}")

        if verbose and stdout_data:
            print(stdout_data)

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

    def get_frame_path(self, n: int = 0, anonymized=False):
        """
        Get the path to the n-th frame extracted from the video file.
        Note that the frame numbering starts at 1 in our naming convention.
        """
        # Adjust index
        n = n + 1

        if anonymized:
            _frame_dir = Path(self.frame_dir)
            frame_dir = _frame_dir.parent / f"anonymized_{_frame_dir.name}"
        else:
            frame_dir = Path(self.frame_dir)
        return frame_dir / f"frame_{n:07d}.jpg"

    def get_frame_paths(self, anonymized=False):
        if anonymized:
            print("Getting anonymized frame paths")
            _frame_dir = Path(self.frame_dir)
            frame_dir = _frame_dir.parent / f"anonymized_{_frame_dir.name}"
        else:
            print("Getting raw frame paths")
            frame_dir = Path(self.frame_dir)

        print(f"Frame dir: {frame_dir}")

        paths = [p for p in frame_dir.glob("*")]
        indices = [int(p.stem.split("_")[1]) for p in paths]
        path_index_tuples = list(zip(paths, indices))
        # sort ascending by index
        path_index_tuples.sort(key=lambda x: x[1])

        if not path_index_tuples:
            return []

        paths, indices = zip(*path_index_tuples)
        paths: List[Path]

        print(
            f"Found {len(paths)} frames for {self.file.name} (anonymized: {anonymized})"
        )

        return paths

    def get_prediction_dir(self):
        return Path(self.prediction_dir)

    def get_predictions_path(self, suffix=".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("predictions").with_suffix(suffix)

    def get_smooth_predictions_path(self, suffix=".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("smooth_predictions").with_suffix(suffix)

    def get_binary_predictions_path(self, suffix=".json"):
        pred_dir = self.get_prediction_dir()
        return pred_dir.joinpath("binary_predictions").with_suffix(suffix)

    def get_raw_sequences_path(self, suffix=".json"):
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
        for key in rois_texts.keys():  # pylint: disable=consider-using-dict-items
            counter = Counter([text for text in rois_texts[key] if text])
            rois_texts[key] = counter.most_common(1)[0][0] if counter else None

        return rois_texts

    def update_text_metadata(self, ocr_frame_fraction=0.001):
        print(f"Updating metadata for {self.file.name}")
        extracted_data_dict = self.extract_text_information(ocr_frame_fraction)
        if extracted_data_dict is None:
            print("No text extracted; skipping metadata update.")
            return
        extracted_data_dict["center_name"] = self.center.name

        ic(extracted_data_dict)

        extracted_data_dict["center_name"] = self.center.name

        print("____________")
        print(extracted_data_dict)
        print("____________")

        self.sensitive_meta = SensitiveMeta.create_from_dict(extracted_data_dict)
        self.state_sensitive_data_retrieved = True  # pylint: disable=attribute-defined-outside-init
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
        # # FIXME
        # fps = 50
        # return fps
        if self.video_meta is None:
            self.update_video_meta()

        if self.video_meta.ffmpeg_meta is None:
            self.video_meta.initialize_ffmpeg_meta(self.file.path)

        return self.video_meta.get_fps()
