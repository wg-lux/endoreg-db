from django.db import models

class Instrument(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Instrument"
        verbose_name_plural = "Instrumente"
