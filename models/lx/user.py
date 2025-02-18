from django.db import models

class LxUserManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    

class LxUser(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    

    # to be extended


