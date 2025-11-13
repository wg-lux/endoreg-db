from typing import TYPE_CHECKING

from django.db import models  #


class EndoscopeManager(models.Manager):
    def get_by_natural_key(self, name, sn):
        return self.get(name=name, sn=sn)


class Endoscope(models.Model):
    objects = EndoscopeManager()

    name = models.CharField(max_length=255)
    sn = models.CharField(max_length=255)
    center = models.ForeignKey("Center", blank=True, null=True, on_delete=models.CASCADE, related_name="endoscopes")
    endoscope_type = models.ForeignKey("EndoscopeType", blank=True, null=True, on_delete=models.CASCADE, related_name="endoscopes")

    if TYPE_CHECKING:
        from endoreg_db.models import Center

        center: models.ForeignKey["Center|None"]
        endoscope_type: models.ForeignKey["EndoscopeType|None"]

    def natural_key(self):
        return (self.name, self.sn)

    def __str__(self):
        return str(self.name)

    class Meta:
        ordering = ["name"]
        verbose_name = "Endoscope"
        verbose_name_plural = "Endoscopes"


class EndoscopeTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class EndoscopeType(models.Model):
    objects = EndoscopeTypeManager()

    name = models.CharField(max_length=255, unique=True)

    if TYPE_CHECKING:
        endoscopes: models.QuerySet["Endoscope"]

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self):
        return str(self.name)

    class Meta:
        ordering = ["name"]
        verbose_name = "Endoscope Type"
        verbose_name_plural = "Endoscope Types"
