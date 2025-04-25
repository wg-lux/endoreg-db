from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import PortalUserInfo

class ProfessionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Profession(models.Model):
    objects = ProfessionManager()
    name = models.CharField(max_length=100)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)


    if TYPE_CHECKING:
        portal_user_infos: models.QuerySet["PortalUserInfo"]

    def __str__(self):
        return str(self.name_de)