from django.db import models


class Report(models.Model):
    name = models.CharField(max_length=100, unique=True)
