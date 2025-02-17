from django.db import models


class CenterManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Center(models.Model):
    objects = CenterManager()

    # import_id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    first_names = models.ManyToManyField(
        "FirstName",
        related_name="centers",
    )
    last_names = models.ManyToManyField("LastName", related_name="centers")

    @classmethod
    def get_by_name(cls, name):
        return cls.objects.get(name=name)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

    def get_first_names(self):
        from endoreg_db.models import FirstName

        names = FirstName.objects.filter(centers=self)
        return names

    def get_last_names(self):
        from endoreg_db.models import LastName

        names = LastName.objects.filter(centers=self)
        return names

    def get_endoscopes(self):
        from endoreg_db.models import Endoscope

        endoscopes = Endoscope.objects.filter(center=self)
        return endoscopes

    # def get_endoscopy_processor
