from django.db import models
from django.utils.text import slugify
from django.utils import timezone

class AnonymizedImageLabel(models.Model):
    """
    Represents a label for anonymized image annotations.

    Attributes:
        name (str): Unique name of the label.
        description (str): Optional description for the label.
    """
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self) -> str:
        return self.name


class AnonymousImageAnnotation(models.Model):
    """
    Represents an annotation for anonymized images.

    Attributes:
        label (ForeignKey): Associated label for the image.
        image_name (str): Name of the image file.
        original_image_url (str): URL of the original image.
        slug (str): Unique identifier for the annotation.
        polyp_count (int): Number of polyps identified in the image.
        comments (str): Additional comments about the annotation.
        gender (str): Gender associated with the annotation (if applicable).
        name_image_url (str): URL of the name image.
        date_created (datetime): Date when the annotation was created.
        processed (bool): Indicates if the annotation has been processed.
    """
    label = models.ForeignKey(AnonymizedImageLabel, on_delete=models.CASCADE)
    image_name = models.CharField(max_length=255)
    original_image_url = models.CharField(max_length=255, default='https://example.com/placeholder.jpg')
    slug = models.SlugField(unique=True, blank=True, null=True)
    polyp_count = models.IntegerField()
    comments = models.TextField(blank=True, null=True)
    gender = models.CharField(max_length=255)
    name_image_url = models.CharField(max_length=255)
    date_created = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        """
        Overrides the save method to generate a slug if not already set.
        """
        if not self.slug:
            self.slug = slugify(f"{self.label}-{self.image_name}")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.image_name} - {self.label}"


class DroppedName(models.Model):
    """
    Represents a dropped name in the annotation process.

    Attributes:
        annotation (ForeignKey): Associated annotation.
        name (str): Dropped name.
        gender (str): Gender associated with the name.
        x (float): X-coordinate of the name.
        y (float): Y-coordinate of the name.
        name_image_url (str): URL of the name image.
        box_coordinates (str): Optional coordinates of the bounding box.
    """
    annotation = models.ForeignKey(AnonymousImageAnnotation, related_name='dropped_names', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    gender = models.CharField(max_length=255)
    x = models.FloatField()
    y = models.FloatField()
    name_image_url = models.CharField(max_length=255)
    box_coordinates = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.gender}) at ({self.x}, {self.y})"


class UploadedFile(models.Model):
    """
    Represents a file uploaded to the system.

    Attributes:
        original_file (FileField): The uploaded original file.
        upload_date (datetime): Date when the file was uploaded.
        description (str): Optional description of the file.
    """
    original_file = models.FileField(upload_to='uploads/original/')
    upload_date = models.DateTimeField(default=timezone.now)
    description = models.TextField(blank=True, null=True)

    def __str__(self) -> str:
        return self.original_file.name


class AnonymizedFile(models.Model):
    """
    Represents an anonymized version of an uploaded file.

    Attributes:
        original_file (OneToOneField): The original uploaded file.
        anonymized_file (FileField): The anonymized file.
        anonymization_date (datetime): Date when the file was anonymized.
    """
    original_file = models.OneToOneField(UploadedFile, on_delete=models.CASCADE, related_name='anonymized_file')
    anonymized_file = models.FileField(upload_to='uploads/anonymized/')
    anonymization_date = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"Anonymized version of {self.original_file.original_file.name}"
