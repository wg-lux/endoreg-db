from django.db import models


class ExaminationTimeTypeManager(models.Manager):
    """
    Manager for ExaminationTimeType with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "ExaminationTimeType":
        return self.get(name=name)


class ExaminationTimeType(models.Model):
    """
    Represents a type of examination time.

    Attributes:
        name (str): The unique name of the examination time type.
        examinations: The examinations associated with this type.
    """

    objects = ExaminationTimeTypeManager()
    name = models.CharField(max_length=100, unique=True)
    examinations = models.ManyToManyField("Examination", blank=True)

    def __str__(self) -> str:
        """
        Return the name of the examination time type as its string representation.
        """
        return self.name

    def natural_key(self) -> tuple:
        """
        Returns the natural key for the examination time type.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)

    class Meta:
        verbose_name = "Examination Time Type"
        verbose_name_plural = "Examination Time Types"
        ordering = ["name"]
