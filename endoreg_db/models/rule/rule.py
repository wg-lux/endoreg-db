from django.db import models

class RuleManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Rule(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    attribute_key = models.CharField(max_length=255)
    rule_type = models.ForeignKey("RuleType", on_delete=models.CASCADE)
    attribute_dtype = models.ForeignKey("RuleAttributeDType", on_delete=models.CASCADE)

    objects = RuleManager()

    class Meta:
        verbose_name = 'Rule'
        verbose_name_plural = 'Rules'

    def natural_key(self):
        """
        Return a tuple representing the natural key for this Rule instance.
        
        Returns:
            tuple: A one-element tuple containing the Rule's unique name.
        """
        return (self.name,)
