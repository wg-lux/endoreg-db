from django.db import models
from rest_framework import serializers

from ..person import Person


class Examiner(Person):
    center = models.ForeignKey(
        "Center", on_delete=models.CASCADE, blank=True, null=True
    )

    def __str__(self):
        return self.first_name + " " + self.last_name


class ExaminerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Examiner
        fields = "__all__"
