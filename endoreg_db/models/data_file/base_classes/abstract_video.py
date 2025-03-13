""" """

from pathlib import Path
from collections import defaultdict, Counter
import shutil
import os
import subprocess
from typing import Optional, List, TYPE_CHECKING, Union
from django.db import models, transaction
from icecream import ic
from tqdm import tqdm
from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.file_operations import get_uuid_filename
from endoreg_db.utils.ocr import extract_text_from_rois

from ....utils.video import (
    transcode_videofile,
    transcode_videofile_if_required,
    initialize_frame_objects,
    extract_frames,
)

from ..metadata import VideoMeta, SensitiveMeta
from .utils import (
    STORAGE_LOCATION,
    VIDEO_DIR,
    FRAME_DIR,
)
from .prepare_bulk_frames import prepare_bulk_frames

if TYPE_CHECKING:
    from endoreg_db.models import (
        VideoPredictionMeta,
        Video,
        RawVideoFile,
        RawVideoPredictionMeta,
        LabelRawVideoSegment,
        LabelVideoSegment,
        ModelMeta,
        Center,
        EndoscopyProcessor,
        VideoMeta,
        Frame,
        RawFrame,
        PatientExamination,
    )  #
    from django.db.models import QuerySet

TEST_RUN = os.environ.get("TEST_RUN", "False")
TEST_RUN = TEST_RUN.lower() == "true"

TEST_RUN_FRAME_NUMBER = int(os.environ.get("TEST_RUN_FRAME_NUMBER", "500"))

if TEST_RUN:
    ic("-----\nTEST RUN ENABLED\n-----")


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
    examination = models.ForeignKey(
        "PatientExamination", on_delete=models.SET_NULL, blank=True, null=True
    )
    original_file_name = models.CharField(max_length=255)
    video_hash = models.CharField(max_length=255, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    frame_dir = models.CharField(max_length=255)
    prediction_dir = models.CharField(max_length=255)
    predictions = models.JSONField(default=dict)
    fps = models.FloatField(blank=True, null=True)
    duration = models.FloatField(blank=True, null=True)
    frame_count = models.IntegerField(blank=True, null=True)

    readable_predictions = models.JSONField(default=dict)
    merged_predictions = models.JSONField(default=dict)
    smooth_merged_predictions = models.JSONField(default=dict)
    binary_smooth_merged_predictions = models.JSONField(default=dict)
    sequences = models.JSONField(default=dict)
    ai_model_meta = models.ForeignKey(
        "ModelMeta", on_delete=models.CASCADE, blank=True, null=True
    )

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

    state_frames_initialized = models.BooleanField(default=False)

    is_raw = models.BooleanField(default=False)

    if TYPE_CHECKING:
        self: Union["RawVideoFile", "Video"]
        label_video_segments: Union[
            "QuerySet[LabelVideoSegment]",
            "QuerySet[LabelRawVideoSegment]",
        ]
        examination: "PatientExamination"
        video_meta: "VideoMeta"
        processor: "EndoscopyProcessor"
        center: "Center"
        ai_model_meta: "ModelMeta"
        sensitive_meta: "SensitiveMeta"
        frames: Union["QuerySet[RawFrame]", "QuerySet[Frame]"]

    class Meta:
        abstract = True  #

    @classmethod
    def transcode_videofile(cls, filepath: Path, transcoded_path: Path):
        """ """
        transcoded_path = transcode_videofile(filepath, transcoded_path)
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
        frame_dir_parent: Path = FRAME_DIR,
        video_dir: Path = VIDEO_DIR,
        save: bool = True,
        frame_paths: List[dict] = None,
    ):
        from endoreg_db.models import Center, EndoscopyProcessor  # pylint: disable=import-outside-toplevel

        video_dir.mkdir(parents=True, exist_ok=True)
        ic(f"Creating {cls} from {file_path}")
        original_file_name = file_path.name

        transcoded_file_path = transcode_videofile_if_required(file_path)
        video_hash = get_video_hash(transcoded_file_path)

        vid_with_hash_exists = cls.check_hash_exists(video_hash=video_hash)  # pylint: disable=no-member
        if vid_with_hash_exists:
            existing: Union["RawVideoFile", "Video"] = cls.objects.get(
                video_hash=video_hash
            )
            ic(f"Existing DB entry found: {existing}")

            return existing

        _new_file_name, uuid = get_uuid_filename(file_path)
        ic(f"No existing DB entry found, creating new with UUID {uuid}")

        try:
            relative_path = transcoded_file_path.relative_to(STORAGE_LOCATION)
        except ValueError as e:
            raise Exception(
                f"{transcoded_file_path} is outside STORAGE_LOCATION {STORAGE_LOCATION}"
            ) from e

        video = cls(
            uuid=uuid,
            file=relative_path.as_posix(),
            center=Center.objects.get(name=center_name),
            processor=EndoscopyProcessor.objects.get(name=processor_name),
            original_file_name=original_file_name,
            video_hash=video_hash,
        )
        video.save()
        if frame_paths:
            ic(f"Initializing frames using provided paths ({len(frame_paths)})")
            video.initialize_frames(frame_paths)

        # Save the instance to the database
        video.save()
        ic(f"Saved {video}")

        return video

    def get_video_model(self):
        from endoreg_db.models import RawVideoFile, Video

        if self.is_raw:
            return RawVideoFile
        return Video

    def get_frame_model(self):
        from endoreg_db.models import RawFrame, Frame

        if self.is_raw_video_file():
            return RawFrame

        return Frame

    def get_label_segment_model(self):
        from endoreg_db.models import LabelVideoSegment, LabelRawVideoSegment  # pylint: disable=import-outside-toplevel

        if self.is_raw:
            return LabelRawVideoSegment
        return LabelVideoSegment

    def sequences_to_label_video_segments(
        self,
        video_prediction_meta: Union["VideoPredictionMeta", "RawVideoPredictionMeta"],
    ):
        """
        Convert sequences to label video segments.
        """
        from endoreg_db.models import Label, InformationSource

        label_video_segment_model = self.get_label_segment_model()
        for label, sequences in self.sequences.items():
            label = Label.objects.get(name=label)
            for sequence in sequences:
                start_frame_number = sequence[0]
                end_frame_number = sequence[1]

                label_video_segment = label_video_segment_model.objects.create(
                    video=self,
                    prediction_meta=video_prediction_meta,
                    label=label,
                    start_frame_number=start_frame_number,
                    end_frame_number=end_frame_number,
                )
                label_video_segment.save()

    def get_frame_number(self):
        """
        Get the number of frames in the video.
        """
        frame_model = self.get_frame_model()
        framecount = frame_model.objects.filter(video=self).count()
        return framecount

    def set_frames_extracted(self, value: bool = True):
        self.state_frames_extracted = value
        self.save()

    def get_frames(self):
        """
        Retrieve all frames for this video in the correct order.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.filter(video=self).order_by("frame_number")

    def get_frame(self, frame_number):
        """
        Retrieve a specific frame for this video.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.get(video=self, frame_number=frame_number)

    def get_frame_range(self, start_frame_number: int, end_frame_number: int):
        """
        Expects numbers of start and stop frame.
        Returns all frames of this video within the given range in ascending order.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.filter(
            video=self,
            frame_number__gte=start_frame_number,
            frame_number__lte=end_frame_number,
        ).order_by("frame_number")

    def is_raw_video_file(self):
        if self.__class__.__name__ == "RawVideoFile":
            return True
        return False

    def get_prediction_meta_model(self):
        from endoreg_db.models import VideoPredictionMeta, RawVideoPredictionMeta

        if self.is_raw_video_file():
            return RawVideoPredictionMeta
        return VideoPredictionMeta

    def predict_video(
        self,
        model_meta_name: str,
        dataset_name: str = "inference_dataset",
        model_meta_version: Optional[int] = None,
        smooth_window_size_s: int = 1,
        binarize_threshold: float = 0.5,
        anonymized_frames: bool = True,
        img_suffix: str = ".jpg",
        test_run: bool = TEST_RUN,
        n_test_frames: int = TEST_RUN_FRAME_NUMBER,
    ):
        """
        WARNING: When using with Video Objects "anonymous_frames" should be set to False
        Predict the video file using the given model.
        Frames should be extracted and anonymized frames should be generated before prediction.
        """
        from endoreg_db.models import (
            RawVideoFile,
            Video,
            ModelMeta,
            AiModel,
        )  # pylint: disable=import-outside-toplevel
        from endo_ai.predictor.inference_dataset import InferenceDataset  # pylint: disable=import-outside-toplevel
        from endo_ai.predictor.model_loader import MultiLabelClassificationNet
        from endo_ai.predictor.predict import Classifier
        from endo_ai.predictor.postprocess import (
            concat_pred_dicts,
            make_smooth_preds,
            find_true_pred_sequences,
        )

        if TEST_RUN:
            test_run = True

        datasets = {
            "inference_dataset": InferenceDataset,
        }

        if isinstance(self, RawVideoFile):
            self: "RawVideoFile"
        elif isinstance(self, Video):
            self: "Video"
        else:
            raise Exception("Invalid instance type")

        dataset_model_class = datasets[dataset_name]

        if anonymized_frames and self.is_raw:
            try:
                frame_dir = self.get_anonymized_frame_dir()
            except:  # FIXME
                frame_dir = Path(self.frame_dir)
                assert self.state_frames_extracted, "Frames not extracted"

        else:
            frame_dir = Path(self.frame_dir)
            assert self.state_frames_extracted, "Frames not extracted"

        model_meta = ModelMeta.get_by_name(model_meta_name, model_meta_version)
        model: AiModel = model_meta.model

        ic(f"Model: {model}, Model Meta: {model_meta}")

        model_type = model.model_type
        model_subtype = model.model_subtype

        ic(f"Model type: {model_type}, Model subtype: {model_subtype}")
        ic(f"self: {self}, Type: {type(self)}")

        prediction_meta_model = self.get_prediction_meta_model()

        ic(
            f"Prediction Meta Model: {prediction_meta_model}, Type: {type(prediction_meta_model)}"
        )

        video_prediction_meta, _created = prediction_meta_model.objects.get_or_create(
            video=self, model_meta=model_meta
        )

        video_prediction_meta.save()

        ic(video_prediction_meta)

        paths = self.get_frame_paths()
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

        self.sequences_to_label_video_segments(
            video_prediction_meta=video_prediction_meta
        )

        ic("Finished inference")
        ic("Saving predictions to DB")
        ic(sequences)
        self.state_initial_prediction_required = False
        self.state_initial_prediction_completed = True
        self.save()

    def get_outside_segments(self, outside_label_name: str = "outside"):
        """
        Get sequences of outside frames.
        """
        from endoreg_db.models import Label  # pylint: disable=import-outside-toplevel

        outside_label = Label.objects.get(name="outside")
        assert outside_label is not None

        outside_segments = self.label_video_segments.filter(label=outside_label)

        ic(f"Getting outside sequences using label: {outside_label}")

        return outside_segments

    def get_outside_frames(self) -> List["Frame"]:
        """
        Get outside frames.
        """
        outside_segments = self.get_outside_segments()
        frames = []
        for segment in outside_segments:
            frames.extend(segment.get_frames())
        return frames

    def get_outside_frame_paths(self):
        """
        Get paths to outside frames.
        """
        frames = self.get_outside_frames()

        frame_paths = [Path(frame.image.path) for frame in frames]

        return frame_paths

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
        assert self.processor is not None, "Processor must be set"
        if not self.fps:
            self.fps = self.get_fps()
        if self.is_raw_video_file():
            self.is_raw = True
        if self.video_meta is None:
            center = self.center
            processor = self.processor
            self.video_meta = VideoMeta.objects.create(
                center=center, processor=processor
            )
            self.video_meta.initialize_ffmpeg_meta(self.file.path)

        if not self.frame_dir:
            self.set_frame_dir()

        sm = self.sensitive_meta
        if sm:
            self.patient = sm.pseudo_patient
            self.patient_examination = sm.pseudo_examination

        super(AbstractVideoFile, self).save(*args, **kwargs)

    def extract_frames(
        self,
        quality: int = 2,
        overwrite: bool = False,
        ext="jpg",
        verbose=False,
    ) -> List[Path]:
        """
        Extract frames from the video file and save them to the frame_dir.
        For this, ffmpeg must be available in in the current environment.
        """

        return extract_frames(
            video=self,
            quality=quality,
            overwrite=overwrite,
            ext=ext,
            verbose=verbose,
        )

    def initialize_frames(self, paths: List[Path]):
        """
        Initialize frame objects for the video file.
        """
        initialize_frame_objects(self, paths)

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

    def get_frame_paths(self):
        """
        Get paths to frames extracted from the video file.
        """
        frames = self.frames.filter(extracted=True).order_by("frame_number")

        paths = [Path(frame.image.path) for frame in frames]
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

        frame_paths = self.get_frame_paths()
        n_frames = len(frame_paths)
        n_frames_to_process = max(1, int(frame_fraction * n_frames))

        # Select evenly spaced frames
        frame_paths = frame_paths[:: n_frames // n_frames_to_process]

        # extract text from each frame and store the value to
        # defaultdict of lists.
        # Then, extract the most frequent value from each list
        # Finally, return the dictionary of most frequent values

        # Create a defaultdict to store the extracted text from each ROI
        rois_texts = defaultdict(list)

        print(f"Processing {n_frames_to_process} frames from {self.file.name}")
        # Process frames
        for frame_path in frame_paths[:n_frames_to_process]:
            extracted_texts = extract_text_from_rois(frame_path, processor)
            for roi, text in extracted_texts.items():
                rois_texts[roi].append(text)

        # Get the most frequent text values for each ROI using Counter
        for key in rois_texts.keys():  # pylint: disable=consider-using-dict-items
            counter = Counter([text for text in rois_texts[key] if text])
            rois_texts[key] = counter.most_common(1)[0][0] if counter else None

        return rois_texts

    def update_text_metadata(self, ocr_frame_fraction=0.001):
        ic(f"Updating metadata for {self.file.name}")
        extracted_data_dict = self.extract_text_information(ocr_frame_fraction)
        if extracted_data_dict is None:
            ic("No text extracted; skipping metadata update.")
            return
        extracted_data_dict["center_name"] = self.center.name

        ic(extracted_data_dict)

        extracted_data_dict["center_name"] = self.center.name

        ic("____________")
        ic(extracted_data_dict)
        ic("____________")

        self.sensitive_meta = SensitiveMeta.create_from_dict(extracted_data_dict)
        self.state_sensitive_data_retrieved = True  # pylint: disable=attribute-defined-outside-init
        self.save()

        # Resulting dict depends on defined ROIs for this processor type!

    def update_video_meta(self):
        video_meta = self.video_meta
        video_path = Path(self.file.path)
        center = self.center
        assert self.processor

        if video_meta is None:
            video_meta = VideoMeta.create_from_file(
                video_path,
                center=center,
                processor=self.processor,
            )
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

    def create_frame_object(
        self, frame_number, image_file=None, extracted: bool = False
    ):
        """
        Returns a frame instance with the image_file set.
        """
        frame_model = self.get_frame_model()
        return frame_model(
            video=self,
            frame_number=frame_number,
            image=image_file,
            extracted=extracted,
        )

    def bulk_create_frames(self, frames_to_create):
        """
        Bulk create frames, then save their images to storage.
        """
        frame_model = self.get_frame_model()
        created = frame_model.objects.bulk_create(frames_to_create)
        # for frame in created:
        #     frame_name = f"frame_{frame.frame_number:07d}.jpg"
        #     frame.image.save(frame_name, frame.image)
        #     frame.save()
