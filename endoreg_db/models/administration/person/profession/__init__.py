from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import PortalUserInfo


class ProfessionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Profession(models.Model):
    objects = ProfessionManager()
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    if TYPE_CHECKING:

        @property
        def portal_user_infos(self) -> models.QuerySet["PortalUserInfo"]: ...

    def __str__(self):
        """
        Return the profession's name as its string representation.
        """
        return str(self.name)
