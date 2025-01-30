from django.db import models

class AglServiceManager(models.Manager):
    # Custom manager for AglService; defines name as natural key
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class AglService(models.Model):
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    online = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects = AglServiceManager()
    devices = models.ManyToManyField("NetworkDevice", related_name="services")
    port = models.IntegerField(blank=True, null=True)
    protocol = models.CharField(max_length=255, blank=True, null=True)
    url = models.URLField(blank=True, null=True)

    def alive_log(self, user, ip_address, user_agent):
        from ..logging import AglServiceLogEntry
        AglServiceLogEntry.objects.create(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            message=f"Service {self.name} is alive.",
            service=self
        )

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']
        
    def natural_key(self):
        return (self.name,)
    
