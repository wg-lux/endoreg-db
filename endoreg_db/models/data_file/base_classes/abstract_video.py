# import cv2
from PIL import Image
from django.core.files.base import ContentFile
from django.db import models, transaction
from tqdm import tqdm
# import cv2
import io
from datetime import date

BATCH_SIZE = 1000

class AbstractVideo(models.Model):
    file = models.FileField(upload_to="raw_videos", blank=True, null=True)
    video_hash = models.CharField(max_length=255, unique=True)
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE, blank=True, null=True)
    date = models.DateField(blank=True, null=True)
    suffix = models.CharField(max_length=255)
    fps = models.FloatField()
    duration = models.FloatField()
    width = models.IntegerField()
    height = models.IntegerField()
    endoscope_image_x = models.IntegerField(blank=True, null=True)
    endoscope_image_y = models.IntegerField(blank=True, null=True)
    endoscope_image_width = models.IntegerField(blank=True, null=True)
    endoscope_image_height = models.IntegerField(blank=True, null=True)
    center = models.ForeignKey("Center", on_delete=models.CASCADE, blank=True, null=True)
    endoscopy_processor = models.ForeignKey("EndoscopyProcessor", on_delete=models.CASCADE, blank=True, null=True)
    frames_extracted = models.BooleanField(default=False)

    meta = models.JSONField(blank=True, null=True)

    class Meta:
        abstract = True

    def get_roi_endoscope_image(self):
        return {
            'x': self.endoscope_image_content_x,
            'y': self.endoscope_image_content_y,
            'width': self.endoscope_image_content_width,
            'height': self.endoscope_image_content_height,
        }

    def initialize_metadata_in_db(self, video_meta=None):
        if not video_meta:
            video_meta = self.meta
        self.set_examination_date_from_video_meta(video_meta)
        self.patient, created = self.get_or_create_patient(video_meta)
        self.save()

    def get_or_create_patient(self, video_meta=None):
        from ...persons import Patient
        if not video_meta:
            video_meta = self.meta

        patient_first_name = video_meta['patient_first_name']
        patient_last_name = video_meta['patient_last_name']
        patient_dob = video_meta['patient_dob']

        # assert that we got all the necessary information
        assert patient_first_name and patient_last_name and patient_dob, "Missing patient information"

        patient, created = Patient.objects.get_or_create(
            first_name=patient_first_name,
            last_name=patient_last_name,
            dob=patient_dob
        )

        return patient, created

    def get_frame_model(self):
        assert 1 == 2, "This method should be overridden in derived classes"

    def get_video_model(self):
        assert 1 == 2, "This method should be overridden in derived classes"

    def get_frame_number(self):
        """
        Get the number of frames in the video.
        """
        frame_model = self.get_frame_model()
        framecount = frame_model.objects.filter(video=self).count()
        return framecount
    
    def set_frames_extracted(self, value:bool=True):
        self.frames_extracted = value
        self.save()
        
    def get_frames(self):
        """
        Retrieve all frames for this video in the correct order.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.filter(video=self).order_by('frame_number')

    def get_frame(self, frame_number):
        """
        Retrieve a specific frame for this video.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.get(video=self, frame_number=frame_number)

    def get_frame_range(self, start_frame_number:int, end_frame_number:int):
        """
        Expects numbers of start and stop frame.
        Returns all frames of this video within the given range in ascending order.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.filter(video=self, frame_number__gte=start_frame_number, frame_number__lte=end_frame_number).order_by('frame_number')

    def _create_frame_object(self, frame_number, image_file):
        frame_model = self.get_frame_model()
        frame = frame_model(
                video=self,
                frame_number=frame_number,
                suffix='jpg',
            )
        frame.image_file = image_file  # Temporary store the file-like object

        return frame

    def _bulk_create_frames(self, frames_to_create):
        frame_model = self.get_frame_model()
        with transaction.atomic():
            frame_model.objects.bulk_create(frames_to_create)

            # After the DB operation, save the ImageField for each object
            for frame in frames_to_create:
                frame_name = f"video_{self.id}_frame_{str(frame.frame_number).zfill(7)}.jpg"
                frame.image.save(frame_name, frame.image_file)

            # Clear the list for the next batch
            frames_to_create = []

    def set_examination_date_from_video_meta(self, video_meta=None):
        if not video_meta:
            video_meta = self.meta
        date_str = video_meta['examination_date'] # e.g. 2020-01-01
        if date_str:
            self.date = date.fromisoformat(date_str)
            self.save()

    def extract_all_frames(self):
        """
        Extract all frames from the video and store them in the database.
        Uses Django's bulk_create for more efficient database operations.
        """
        # Open the video file
        video = cv2.VideoCapture(self.file.path)

        # Initialize video properties
        self.initialize_video_specs(video)

        # Prepare for batch operation
        frames_to_create = []

        # Extract frames
        for frame_number in tqdm(range(int(self.duration * self.fps))):
            # Read the frame
            success, image = video.read()
            if not success:
                break

            # Convert the numpy array to a PIL Image object
            pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

            # Save the PIL Image to a buffer
            buffer = io.BytesIO()
            pil_image.save(buffer, format='JPEG')

            # Create a file-like object from the byte data in the buffer
            image_file = ContentFile(buffer.getvalue())

            # Prepare Frame instance (don't save yet)
            frame = self._create_frame_object(frame_number, image_file)
            frames_to_create.append(frame)

            # Perform bulk create when reaching BATCH_SIZE
            if len(frames_to_create) >= BATCH_SIZE:
                self._bulk_create_frames(frames_to_create)
                frames_to_create = []


        # Handle remaining frames
        if frames_to_create:
            self._bulk_create_frames(frames_to_create)
            frames_to_create = []

        # Close the video file
        video.release()
        self.set_frames_extracted(True)


    def initialize_video_specs(self, video):
        """
        Initialize and save video metadata like framerate, dimensions, and duration.
        """
        self.fps = video.get(cv2.CAP_PROP_FPS)
        self.width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.duration = video.get(cv2.CAP_PROP_FRAME_COUNT) / self.fps
        self.save()