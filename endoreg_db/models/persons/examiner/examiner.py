from django.db import models
from ..person import Person
from rest_framework import serializers
from endoreg_db.utils import get_hash_string

import os
from icecream import ic

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

        assert isinstance(center, Center), (
            f"center must be an instance of Center, not {type(center)}"
        )
        hash = get_hash_string(
            first_name=first_name,  #
            last_name=last_name,
            center_name=center.name,
            salt=SALT,
        )
        examiner, created = cls.objects.get_or_create(hash=hash)
        return examiner, created


class ExaminerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Examiner
        fields = "__all__"
