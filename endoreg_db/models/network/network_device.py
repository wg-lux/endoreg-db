# Django db class to store network devices (e.g., servers, clients, switches, etc.)

from django.db import models

class NetworkDeviceManager(models.Manager):
    # Custom manager for NetworkDevice; defines name as natural key
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class NetworkDevice(models.Model):
    name = models.CharField(max_length=255)
    ip = models.GenericIPAddressField(blank=True, null=True)
    description = models.CharField(max_length=255)
    device_type = models.ForeignKey("NetworkDeviceType", on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects = NetworkDeviceManager()

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'network_devices'
        ordering = ['name']
        
    def natural_key(self):
        return (self.name,)