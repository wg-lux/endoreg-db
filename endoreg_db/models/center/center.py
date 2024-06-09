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
        'FirstName',
        related_name='centers',
    )
    last_names = models.ManyToManyField('LastName', related_name='centers')

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name