# Insert models for Client, Client Type, Client Tag

from django.db import models

class LxClientTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
#TODO implement base population for client Type

class LxClientType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    abbreviation = models.CharField(max_length=10, unique=True)  # New field
    description = models.TextField(blank=True)
    objects = LxClientTypeManager()
    
    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)
    

class LxClientTagManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
#TODO implement base population for client Tag

class LxClientTag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    objects = LxClientTagManager()

    # M2M relationship with LxPermission
    permissions = models.ManyToManyField('LxPermission', blank=True, related_name="allowed_client_tags")
    
    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)

class LxClientManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
#TODO implement base population for client

class LxClient(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    client_type = models.ForeignKey(LxClientType, on_delete=models.CASCADE)
    client_tags = models.ManyToManyField(LxClientTag, blank=True)
    vpn_ip = models.GenericIPAddressField(blank=True, null=True)
    objects = LxClientManager()

