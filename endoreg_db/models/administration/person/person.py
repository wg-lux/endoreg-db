from __future__ import annotations

from abc import abstractmethod
from datetime import date
from typing import TypeAlias, Any

from django.db import models

from ...other.gender import Gender

NoPersonValue: TypeAlias = None
PersonTextValue: TypeAlias = str | NoPersonValue
PersonDateValue: TypeAlias = date | NoPersonValue
PersonGenderValue: TypeAlias = "Gender | NoPersonValue"


class Person(models.Model):
    """
    Abstract base class for a person.

    Attributes:
        first_name (str): The first name of the person.
        last_name (str): The last name of the person.
        dob (date): The date of birth of the person.
        gender (Gender): The gender of the person.
        email (str): The email address of the person.
        phone (str): The phone number of the person.
    """

    first_name: models.CharField[Any, Any] = models.CharField(max_length=255)
    last_name: models.CharField[Any, Any] = models.CharField(max_length=255)
    dob: models.DateField[Any, Any] = models.DateField(
        "Date of Birth",
        blank=True,
        null=True,
    )
    gender: models.ForeignKey[Any] = models.ForeignKey(
        "endoreg_db.Gender", on_delete=models.SET_NULL, null=True
    )
    email: models.EmailField[Any, Any] = models.EmailField(
        max_length=255,
        blank=True,
        null=True,
    )
    phone: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    is_real_person: models.BooleanField[Any, Any] = models.BooleanField(default=True)

    post_code: models.CharField[Any, Any] = models.CharField(
        max_length=20,
        blank=True,
        null=True,
    )
    city: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    street: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    @abstractmethod
    def __str__(self) -> str: ...

    class Meta:
        abstract = True
