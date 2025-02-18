from django.db import models

class LxIdentityTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
#TODO implement base population for identity Type
## ed25519, rsa, ecdsa, dsa, etc

class LxIdentityType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    objects = LxIdentityTypeManager()
    
    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)

class LxIdentity(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    identity_type = models.ForeignKey(LxIdentityType, on_delete=models.CASCADE)
    objects = models.Manager()

    # Foreign key to user, nullable
    user = models.ForeignKey('LxUser', on_delete=models.CASCADE, null=True, blank=True)
    
    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)
