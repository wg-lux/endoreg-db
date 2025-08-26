from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import RequirementSet

class TagManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)

    objects = TagManager()
    
    if TYPE_CHECKING:
        requirement_sets: "models.ManyToManyField[RequirementSet]"

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)