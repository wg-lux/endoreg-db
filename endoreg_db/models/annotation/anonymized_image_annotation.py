from django.db import models
from django.utils.text import slugify

class AnonymizedImageLabel(models.Model):
    name = models.CharField(max_length=255, unique=True)  # Ensuring label names are unique
    description = models.TextField(blank=True, null=True)  # Optional description field

    def __str__(self):
        return self.name

class AnonymousImageAnnotation(models.Model):
    label = models.ForeignKey(AnonymizedImageLabel, on_delete=models.CASCADE)
    image_name = models.CharField(max_length=255)
    original_image_url = models.CharField(max_length=255, default='https://example.com/placeholder.jpg') # Field for the original image
    slug = models.SlugField(unique=True, blank=True, null=True)
    polyp_count = models.IntegerField()
    comments = models.TextField(blank=True, null=True)  # Comments can be longer
    gender = models.CharField(max_length=255)  # Overall gender (if applicable)
    name_image_url = models.CharField(max_length=255)
    date_created = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)  # Track if this annotation has been processed

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.label}-{self.image_name}")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.image_name} - {self.label}"

class DroppedName(models.Model):
    annotation = models.ForeignKey(AnonymousImageAnnotation, related_name='dropped_names', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    gender = models.CharField(max_length=255)  # Gender of the individual name
    x = models.FloatField()
    y = models.FloatField()
    name_image_url = models.CharField(max_length=255)
    box_coordinates = models.CharField(max_length=255, blank=True, null=True)  # Store box coordinates if needed

    def __str__(self):
        return f"{self.name} ({self.gender}) at ({self.x}, {self.y})"

from django.db import models
from django.utils import timezone

class UploadedFile(models.Model):
    original_file = models.FileField(upload_to='uploads/original/')
    upload_date = models.DateTimeField(default=timezone.now)
    description = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return self.original_file.name
    
class AnonymizedFile(models.Model):
    original_file = models.OneToOneField(UploadedFile, on_delete=models.CASCADE, related_name='anonymized_file')
    anonymized_file = models.FileField(upload_to='uploads/anonymized/')
    anonymization_date = models.DateTimeField(default=timezone.now)
    
    def __str__(self):
        return f"Anonymized version of {self.original_file.original_file.name}"
