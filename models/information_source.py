from django.db import models

def get_prediction_information_source():
    _source = InformationSource.objects.get(name="prediction")

    # make sure to return only one object
    assert _source, "No prediction information source found"
    return _source

class InformationSourceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class InformationSource(models.Model):
    objects = InformationSourceManager()

    name = models.CharField(max_length=100)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)

    url = models.URLField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    date = models.DateField(blank=True, null=True)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
