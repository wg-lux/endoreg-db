from django.db import models

class LabelTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class LabelType(models.Model):
    """
    A class representing a label type.

    Attributes:
        name (str): The name of the label type.
        description (str): A description of the label type.

    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    objects = LabelTypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name


class LabelManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Label(models.Model):
    """
    A class representing a label.

    Attributes:
        name (str): The name of the label.
        label_type (LabelType): The type of the label.
        description (str): A description of the label.

    """
    name = models.CharField(max_length=255)
    label_type = models.ForeignKey("LabelType", on_delete=models.CASCADE, related_name="labels")
    description = models.TextField(blank=True, null=True)

    objects = LabelManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

class LabelSetManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class LabelSet(models.Model):
    """
    A class representing a label set.

    Attributes:
        name (str): The name of the label set.
        description (str): A description of the label set.

    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    version = models.IntegerField()
    labels = models.ManyToManyField("Label", related_name="labels")
    
    objects = LabelSetManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
    def get_labels_in_order(self):
        """
        Get all labels in this label set as list in the correct order.
        """
        return list(self.labels.all().order_by('id'))
