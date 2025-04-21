from django.db import models


class RuleAttributeDtypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class RuleAttributeDType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = RuleAttributeDtypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name