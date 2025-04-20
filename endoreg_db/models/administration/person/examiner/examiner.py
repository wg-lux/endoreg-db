from django.db import models
from ..person import Person
from endoreg_db.utils import get_examiner_hash, create_mock_examiner_name
from typing import TYPE_CHECKING

from ....utils import DJANGO_NAME_SALT
if TYPE_CHECKING:
    from endoreg_db.models import Center
class Examiner(Person):
    center = models.ForeignKey(
        "Center", on_delete=models.CASCADE, blank=True, null=True
    )
    hash = models.CharField(max_length=255, unique=True)

    if TYPE_CHECKING:
        center: "Center"

    def __str__(self):
        return self.first_name + " " + self.last_name

    @classmethod
    def custom_get_or_create(cls, first_name: str, last_name: str, center: "Center"):
        created = False

        _hash = get_examiner_hash(
            first_name=first_name,  #
            last_name=last_name,
            center_name=center.name,
            salt=DJANGO_NAME_SALT,
        )

        examiner_exists = cls.objects.filter(hash=_hash).exists()
        if examiner_exists:
            examiner = cls.objects.get(hash=_hash)

        else:
            first_name, last_name = create_mock_examiner_name()
            examiner = cls.objects.create(
                first_name=first_name,
                last_name=last_name,
                center=center,
                hash=_hash,
            )
            examiner.save()
            created = True
        return examiner, created
