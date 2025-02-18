from django.db import models
from pathlib import Path
from collections import defaultdict, Counter

from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.file_operations import get_uuid_filename
from endoreg_db.utils.ocr import extract_text_from_rois
from django.core.validators import FileExtensionValidator
from django.core.files.storage import FileSystemStorage
import shutil
import os
import subprocess
from django.conf import settings
from typing import List
from endoreg_db.utils.validate_endo_roi import validate_endo_roi
import warnings

from ..metadata import VideoMeta, SensitiveMeta
from tqdm import tqdm

def anonymize_frame(raw_frame_path:Path, target_frame_path:Path, endo_roi):
    """
    Anonymize the frame by blacking out all pixels that are not in the endoscope ROI.
    """
    import cv2
    import numpy as np

    frame = cv2.imread(raw_frame_path.as_posix())

    # make black frame with same size as original frame
    new_frame = np.zeros_like(frame)

    # endo_roi is dict with keys "x", "y", "width", "heigth"
    x = endo_roi["x"]
    y = endo_roi["y"]
    width = endo_roi["width"]
    height = endo_roi["height"]

    # copy endoscope roi to black frame
    new_frame[y:y+height, x:x+width] = frame[y:y+height, x:x+width]
    cv2.imwrite(target_frame_path.as_posix(), new_frame)

    return frame


# get DJANGO_NAME_SALT from environment
DJANGO_NAME_SALT = os.environ.get('DJANGO_NAME_SALT', 'default_salt')

def copy_with_progress(src, dst, buffer_size=1024*1024):
    total_size = os.path.getsize(src)
    copied_size = 0

    with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
        while True:
            buf = fsrc.read(buffer_size)
            if not buf:
                break
            fdst.write(buf)
            copied_size += len(buf)
            progress = copied_size / total_size * 100
            print(f"\rProgress: {progress:.2f}%", end='')


PSEUDO_DIR:Path = getattr(settings, 'PSEUDO_DIR', settings.BASE_DIR / 'erc_data')

STORAGE_LOCATION = PSEUDO_DIR
RAW_VIDEO_DIR_NAME = 'raw_videos'
RAW_VIDEO_DIR = STORAGE_LOCATION / RAW_VIDEO_DIR_NAME

if not RAW_VIDEO_DIR.exists():
    RAW_VIDEO_DIR.mkdir(parents=True)


class RawVideoFile(models.Model):
    uuid = models.UUIDField()
    file = models.FileField(
        upload_to="RAW_VIDEO_DIR_NAME",
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        storage=FileSystemStorage(location=STORAGE_LOCATION.resolve().as_posix()),
    )
    
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
    state_anonymized_frames_generated = models.BooleanField(default=False)
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
        center_name: str,
        processor_name: str,
        frame_dir_parent: Path = Path("erc_data/raw_frames"),
        video_dir: Path=Path("erc_data/raw_videos"),
        delete_source: bool = False,
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
        # make sure that no other file with the same hash exists
        if cls.objects.filter(video_hash=video_hash).exists():
            # log and print warnint
            print(f"File with hash {video_hash} already exists")
            return None

        center = Center.objects.get(name=center_name)
        assert center is not None, "Center must exist"

        processor = EndoscopyProcessor.objects.get(name=processor_name)
        assert processor is not None, "Processor must exist"

        new_filepath = video_dir / new_file_name

        print(f"Copy {file_path} to {new_filepath}")
        copy_with_progress(file_path.resolve().as_posix(), new_filepath.resolve().as_posix())
        print(f"\nCopied to {new_filepath}")

        # Make sure file was transferred correctly and hash is correct
        if not new_filepath.exists():
            print(f"File {file_path} was not transferred correctly to {new_filepath}")
            return None

        new_hash = get_video_hash(new_filepath)
        if new_hash != video_hash:
            print(f"Hash of file {file_path} is not correct")
            return None

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

        if delete_source:
            file_path.unlink()

        return raw_video_file

    def generate_anonymized_frames(self):
        """
        Generate anonymized frames from the video file.
        """
        if not self.state_frames_extracted:
            print(f"Frames not extracted for {self.file.name}")
            return None
        
        frame_dir = Path(self.frame_dir)
        anonymized_frame_dir = frame_dir.parent / f"anonymized_{self.uuid}"
        if not anonymized_frame_dir.exists():
            anonymized_frame_dir.mkdir(parents=True, exist_ok=True)
        
        # make sure, that the directory is empty
        for f in tqdm(anonymized_frame_dir.glob("*")):
            f.unlink()

        endo_roi = self.get_endo_roi()
        assert validate_endo_roi(endo_roi), "Endoscope ROI is not valid"

        # anonymize frames: copy endo-roi content while making other pixels black. (frames are Path objects to jpgs or pngs)
        for frame_path in self.get_frame_paths():
            frame_name = frame_path.name
            target_frame_path = anonymized_frame_dir / frame_name
            anonymize_frame(frame_path, target_frame_path, endo_roi)

        self.state_anonymized_frames_generated = True

        return f"Anonymized frames generated to {anonymized_frame_dir}"

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
        endo_roi = self.video_meta.get_endo_roi()
        return endo_roi

    def set_frame_dir(self):
        self.frame_dir = f"{RAW_VIDEO_DIR}/{self.uuid}"

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
        
        super(RawVideoFile, self).save(*args, **kwargs)

    def extract_frames(
        self,
        quality: int = 2,
        frame_dir: Path = None,
        overwrite: bool = False,
        ext="jpg",
        verbose=False
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
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        # Display progress
        
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break

            if verbose:
                if output:
                    print(output.strip())
        
        if process.returncode != 0:
            raise Exception(f"Error extracting frames: {process.stderr.read()}")

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

        paths = [p for p in frame_dir.glob('*')]
        indices = [int(p.stem.split("_")[1]) for p in paths]
        path_index_tuples = list(zip(paths, indices))
        # sort ascending by index
        path_index_tuples.sort(key=lambda x: x[1])
        
        if not path_index_tuples:
            return []
        
        paths, indices = zip(*path_index_tuples)
        paths:List[Path]

        print(f"Found {len(paths)} frames for {self.file.name} (anonymized: {anonymized})")

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
        extracted_data_dict = self.extract_text_information(ocr_frame_fraction)
        extracted_data_dict["center_name"] = self.center.name

        print("____________")
        print(extracted_data_dict)
        print("____________")

        self.sensitive_meta = SensitiveMeta.create_from_dict(extracted_data_dict)
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
