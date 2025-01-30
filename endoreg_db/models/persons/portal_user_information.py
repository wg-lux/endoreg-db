from django.db import models

# models.py in your main app
from django.contrib.auth.models import User

class ProfessionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Profession(models.Model):
    objects = ProfessionManager()
    name = models.CharField(max_length=100)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name_de

class PortalUserInfo(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    profession = models.ForeignKey('Profession', on_delete=models.CASCADE, blank=True, null=True)
    works_in_endoscopy = models.BooleanField(blank=True, null=True)
    # Add other fields as needed

    def __str__(self):
        return self.user.username
