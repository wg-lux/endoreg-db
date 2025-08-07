from django.db import models

class RulesetManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Ruleset(models.Model):
    name = models.CharField(max_length=255, unique=True)
    rules = models.ManyToManyField('Rule')

    objects = RulesetManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
