# Django db class to store network device types (e.g., server, client, switch, etc.)

from django.db import models

class NetworkDeviceTypeManager(models.Manager):
    # Custom manager for NetworkDeviceType; defines name as natural key
    def get_by_natural_key(self, name):
        return self.get(name=name)

class NetworkDeviceType(models.Model):
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects = NetworkDeviceTypeManager()

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'network_device_types'
        ordering = ['name']

