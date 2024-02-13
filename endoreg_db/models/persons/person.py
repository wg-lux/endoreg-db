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
    gender = models.CharField(
        max_length=10, choices=[('Male', 'Male'), ('Female', 'Female'), ('Other', 'Other')],
        blank=True, null=True
    )
    email = models.EmailField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=255, blank=True, null=True)
    is_real_person = models.BooleanField(default=True)

    @abstractmethod
    def __str__(self):
        pass

    class Meta:
        abstract = True

