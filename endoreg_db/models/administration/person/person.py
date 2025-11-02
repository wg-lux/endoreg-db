from abc import abstractmethod
from django.db import models

class Person(models.Model):
    """
    Abstract base class for a person.

    Attributes:
        first_name (str): The first name of the person.
        last_name (str): The last name of the person.
        dob (date): The date of birth of the person.
        gender (str): The gender of the person.
        email (str): The email address of the person.
        phone (str): The phone number of the person.
    """

    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    dob = models.DateField("Date of Birth", blank=True, null=True)
    gender = models.ForeignKey("Gender", on_delete=models.SET_NULL, null=True)
    email = models.EmailField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=255, blank=True, null=True)
    is_real_person = models.BooleanField(default=True)

    post_code = models.CharField(max_length=20, blank=True, null=True)
    city = models.CharField(max_length=255, blank=True, null=True)
    street = models.CharField(max_length=255, blank=True, null=True)

    @abstractmethod
    def __str__(self):
        pass

    class Meta:
        abstract = True

