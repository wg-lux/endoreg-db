from django.db import models

class RuleTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class RuleType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = RuleTypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Rule Type'
        verbose_name_plural = 'Rule Types'