from django.db import models


def get_prediction_information_source():
    """
    Retrieves the prediction information source.
    
    Fetches the InformationSource object with the name "prediction" from the database.
    Asserts that the source exists, raising an AssertionError if not found.
    
    Returns:
        InformationSource: The information source object with name "prediction".
    """
    _source = InformationSource.objects.get(name="prediction")

    # make sure to return only one object
    assert _source, "No prediction information source found"
    return _source


class InformationSourceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves the model instance matching the provided natural key.
        
        Args:
            name: The natural key value corresponding to the object's 'name' field.
        
        Returns:
            The model instance whose 'name' attribute equals the given key.
        """
        return self.get(name=name)


class InformationSource(models.Model):
    objects = InformationSourceManager()

    name = models.CharField(max_length=100)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)

    url = models.URLField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    date = models.DateField(blank=True, null=True)

    def natural_key(self):
        """
        Return the natural key for this instance.
        
        The natural key is defined as a tuple containing the name attribute.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the information source.
        
        The 'name' attribute is explicitly converted to a string to ensure a consistent return type.
        """
        return str(self.name)
