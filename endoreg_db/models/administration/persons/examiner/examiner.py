from django.db import models
from ..person import Person
from rest_framework import serializers
from endoreg_db.utils import get_examiner_hash

import os
# from icecream import ic
# from datetime import date

# get DJANGO_SALT from environment
SALT = os.getenv("DJANGO_SALT", "default_salt")


class Examiner(Person):
    center = models.ForeignKey(
        "Center", on_delete=models.CASCADE, blank=True, null=True
    )
    hash = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.first_name + " " + self.last_name

    @classmethod
    def custom_get_or_create(cls, first_name: str, last_name: str, center: "Center"):
        from endoreg_db.models import Center
        from endoreg_db.utils import create_mock_examiner_name

        assert isinstance(center, Center), (
            f"center must be an instance of Center, not {type(center)}"
        )
        created = False

        hash = get_examiner_hash(
            first_name=first_name,  #
            last_name=last_name,
            center_name=center.name,
            salt=SALT,
        )

        examiner_exists = cls.objects.filter(hash=hash).exists()
        if examiner_exists:
            examiner = cls.objects.get(hash=hash)

        else:
            first_name, last_name = create_mock_examiner_name()
            examiner = cls.objects.create(
                first_name=first_name,
                last_name=last_name,
                center=center,
                hash=hash,
            )
            examiner.save()
            created = True
        return examiner, created


class ExaminerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Examiner
        fields = "__all__"
