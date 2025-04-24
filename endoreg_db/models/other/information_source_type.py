from django.db import models

class InformationSourceType(models.Model):
    # Name of the source type, e.g. 'guideline', 'guideline-chapter', etc.
    name = models.CharField(max_length=100)

    # Version name, manually defined (e.g., 'v1.0', '2024.04') — CharField allows flexible naming
    version = models.CharField(max_length=50)

    # Internal numeric versioning, auto-increments within each name group
    # Optional on creation (blank=True, null=True); will be auto-set in save()
    version_intern = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        # Ensures no duplicate (name, version_intern) pairs exist
        unique_together = ("name", "version_intern")
        verbose_name = "Information Source Type"
        verbose_name_plural = "Information Source Types"

    def save(self, *args, **kwargs):
        # Auto-assign version_intern if not manually provided
        if self.version_intern is None:
            # Fetch the latest version_intern for the same name group
            last = InformationSourceType.objects.filter(name=self.name).order_by('-version_intern').first()
            # Start at 1 if no previous entries, otherwise increment the highest one
            self.version_intern = 1 if not last else last.version_intern + 1

        # Save the model instance normally
        super().save(*args, **kwargs)

    def __str__(self):
        # String representation showing type and versioning
        return f"{self.name} (v{self.version}, internal {self.version_intern})"

    """
    Example usage:

    InformationSourceType.objects.create(name="guideline", version="v1.0")
    Will auto-assign version_intern = 1

    InformationSourceType.objects.create(name="guideline", version="v1.1")
     Will auto-assign version_intern = 2

    InformationSourceType.objects.create(name="guideline-chapter", version="v1.0")
    Will auto-assign version_intern = 1 (new group)

    InformationSourceType.objects.create(name="guideline", version="v1.2", version_intern=5)
    You can still manually override version_intern
    """
