from typing import TYPE_CHECKING

from django.db import models

from endoreg_db.utils import create_mock_examiner_name, get_examiner_hash

from ....utils import DJANGO_NAME_SALT
from ..person import Person

if TYPE_CHECKING:
    from endoreg_db.models import administration


class Examiner(Person):
    center = models.ForeignKey("Center", on_delete=models.CASCADE, blank=True, null=True)
    hash = models.CharField(max_length=255, unique=True)

    if TYPE_CHECKING:
        center: models.ForeignKey["administration.Center|None"]
        portal_user_info: models.OneToOneField["administration.PortalUserInfo"]

    def __str__(self):
        return self.first_name + " " + self.last_name

    @classmethod
    def custom_get_or_create(cls, first_name: str, last_name: str, center: "administration.Center", substitute_names: bool = True):
        from endoreg_db.models import (
            FirstName,
            LastName,
        )

        if isinstance(first_name, FirstName):
            first_name = str(first_name.name)

        if isinstance(last_name, LastName):
            last_name = str(last_name.name)

        real_hash = get_examiner_hash(
            first_name=first_name,
            last_name=last_name,
            center_name=center.name,
            salt=DJANGO_NAME_SALT,
        )

        if substitute_names:
            name_tuple = create_mock_examiner_name()

        else:
            name_tuple = (first_name, last_name)
        defaults = dict(
            first_name=name_tuple[0],
            last_name=name_tuple[1],
            center=center,
        )
        examiner, created = cls.objects.get_or_create(hash=real_hash, defaults=defaults)
        return examiner, created
