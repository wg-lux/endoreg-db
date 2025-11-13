from django.db import models


class ResourceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Resource(models.Model):
    objects = ResourceManager()

    name = models.CharField(max_length=255)

    def natural_key(self):
        """
        Return a tuple representing the natural key for this resource instance.

        Returns:
            tuple: A one-element tuple containing the resource's name.
        """
        return (self.name,)

    def __str__(self):
        return self.name
