import hashlib
from django.db import models

def hash_name(name):
    """Generate a hash for a given name using SHA-256."""
    hash_object = hashlib.sha256(name.encode())
    return hash_object.hexdigest()

class NameManager(models.Manager):
    def get_by_natural_key(self, name):
        hashed_name = hash_name(name)
        return self.get(name_hash=hashed_name)

