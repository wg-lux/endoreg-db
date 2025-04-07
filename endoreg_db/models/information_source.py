from django.db import models


def get_prediction_information_source():
    """
    Retrieves the prediction information source.
    
    This function queries the InformationSource model for an object with the name "prediction".
    It asserts the existence of the source and returns it.
    """
    _source = InformationSource.objects.get(name="prediction")

    # make sure to return only one object
    assert _source, "No prediction information source found"
    return _source


class InformationSourceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve an InformationSource by its natural key.
        
        Looks up and returns the InformationSource instance whose name matches
        the provided natural key.
        
        Args:
            name: The natural key identifying the InformationSource.
        
        Returns:
            The matching InformationSource object.
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
        """Return the natural key for this information source as a tuple.
        
        The returned tuple contains the instance's name, which is used as its natural identifier.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the information source.
        
        This method returns the name attribute converted to a string.
        
        Returns:
            str: The name attribute as a string.
        """
        return str(self.name)
