import random
from typing import TYPE_CHECKING, Any, TypeAlias

import numpy as np
from django.db import models

# Corrected imports for type hints
if TYPE_CHECKING:
    from lx_dtypes.models.ledger.p_finding_classification_choice_descriptor import (
        PFindingClassificationChoiceDescriptorDataDict,
    )

    JsonObjectMap: TypeAlias = dict[str, dict[str, Any]]
    DescriptorValueMap: TypeAlias = dict[
        str, "PFindingClassificationChoiceDescriptorDataDict" | dict[str, Any]
    ]


class PatientFindingClassification(models.Model):
    """Represents basic classifications for specific findings in a patient context.
    Links a PatientFinding to a specific classification and choice, with optional subcategory values.
    """

    finding = models.ForeignKey(
        "PatientFinding", on_delete=models.CASCADE, related_name="classifications"
    )
    classification = models.ForeignKey(
        "FindingClassification",
        on_delete=models.CASCADE,
        related_name="patient_finding_classifications",
    )
    classification_choice = models.ForeignKey(
        "FindingClassificationChoice",
        on_delete=models.CASCADE,
        related_name="patient_finding_classifications",
    )

    is_active = models.BooleanField(
        default=True, help_text="Indicates if the classification is currently active."
    )
    subcategories = models.JSONField(blank=True, null=True)
    numerical_descriptors = models.JSONField(blank=True, null=True)

    if TYPE_CHECKING:
        pass

    class Meta:
        verbose_name = "Patient Finding Classification"
        verbose_name_plural = "Patient Finding Classifications"
        ordering = ["finding", "classification", "classification_choice"]

    def __str__(self):
        """
        Return a string representation combining the finding, classification, and classification choice.
        """
        return f"{self.finding} - {self.classification} - {self.classification_choice}"

    def save(self, *args, **kwargs):
        """
        Saves the model instance after validating and initializing classification-related fields.

        Ensures that the selected classification choice is valid for the associated classification. If subcategories or numerical descriptors are unset, initializes them from the classification choice before saving.
        """
        if self.classification_choice not in self.classification.choices.all():
            raise ValueError("classification_choice must be in classification.choices")

        if not self.subcategories:
            self.subcategories = self.classification_choice.subcategories

        if not self.numerical_descriptors:
            self.numerical_descriptors = (
                self.classification_choice.numerical_descriptors
            )

        super().save(*args, **kwargs)

    def initialize_and_get_subcategories(self) -> "JsonObjectMap":
        """
        Ensure the subcategories field is initialized and return its dictionary.

        Returns:
            dict: The subcategories associated with this classification.
        """
        if not self.subcategories:
            self.save()
        assert self.subcategories is not None
        return self.subcategories

    def initialize_and_get_descriptors(self) -> "DescriptorValueMap":
        """
        Return the numerical descriptors dictionary, initializing it if necessary.

        If the `numerical_descriptors` field is empty or uninitialized, the method triggers model initialization and returns the resulting dictionary.
        """
        if not self.numerical_descriptors:
            self.save()
        assert self.numerical_descriptors is not None
        return self.numerical_descriptors

    def set_subcategory(
        self, subcategory_name: str, subcategory_value: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Update the value of a specified subcategory and save the classification.

        Parameters:
            subcategory_name (str): The name of the subcategory to update.
            subcategory_value (dict): The value to assign to the subcategory.

        Returns:
            dict: The updated subcategory dictionary.
        """
        assert self.subcategories, "Subcategories must be initialized."
        assert subcategory_name in self.subcategories, (
            "Subcategory must be in subcategories."
        )
        self.subcategories[subcategory_name]["value"] = subcategory_value
        self.save()

        return self.subcategories[subcategory_name]

    def set_random_subcategories(self) -> "JsonObjectMap":
        """
        Assign random values to all required subcategories that do not already have a value.

        For each required subcategory without a value, selects a random option from its available choices, updates the subcategory, saves the model, and returns the updated subcategories dictionary.

        Returns:
            dict: The updated subcategories with random values assigned where needed.
        """

        if not self.subcategories or not self.numerical_descriptors:
            self.save()

        self.refresh_from_db()
        assert self.subcategories is not None, "Subcategories must be initialized."

        for subcategory_name, subcategory_dict in self.subcategories.items():
            if subcategory_dict["required"] and not subcategory_dict.get("value", None):
                subcategory_choice = random.choice(subcategory_dict["choices"])
                self.subcategories[subcategory_name]["value"] = subcategory_choice

        self.save()

        return self.subcategories

    def get_random_value_for_numerical_descriptor(self, descriptor_name: str) -> float:
        """
        Generate a random value for the specified numerical descriptor using its defined distribution parameters.

        Parameters:
            descriptor_name (str): The name of the numerical descriptor to generate a value for.

        Returns:
            float: A randomly generated value based on the descriptor's distribution, clipped to its min and max range.

        Raises:
            ValueError: If the descriptor's distribution type is not supported.
        """
        assert self.numerical_descriptors is not None, (
            "Numerical descriptors must be initialized."
        )
        assert descriptor_name in self.numerical_descriptors, (
            "Descriptor must be in numerical descriptors."
        )
        descriptor = self.numerical_descriptors[descriptor_name]
        min_val = descriptor.get("min", 0)
        max_val = descriptor.get("max", 1)
        distribution = descriptor.get("distribution", "normal")
        if distribution == "normal":
            mean = descriptor.get("mean", 0.5)
            std = descriptor.get("std", 0.1)
            value = np.random.normal(mean, std)
            # clip value to min and max
            value = np.clip(value, min_val, max_val)
        elif distribution == "uniform":
            value = np.random.uniform(min_val, max_val)
        else:
            raise ValueError("Distribution not supported")

        return value

    def set_random_numerical_descriptor(
        self, descriptor_name: str, save: bool = True
    ) -> dict[str, Any]:
        """
        Assigns a random value to the specified numerical descriptor and optionally saves the model.

        Parameters:
            descriptor_name (str): The name of the numerical descriptor to update.
            save (bool): If True, saves the model after updating the descriptor. Defaults to True.

        Returns:
            dict: The updated numerical descriptor dictionary with the new random value.

        Raises:
            ValueError: If the descriptor name is not present in the numerical descriptors.
        """
        if self.numerical_descriptors is None:
            self.save()
        assert self.numerical_descriptors is not None, (
            "Numerical descriptors must be initialized."
        )
        if descriptor_name not in self.numerical_descriptors:
            raise ValueError("Descriptor name must be in numerical descriptors.")

        value = self.get_random_value_for_numerical_descriptor(descriptor_name)
        self.numerical_descriptors[descriptor_name]["value"] = value
        if save:
            self.save()

        return self.numerical_descriptors[descriptor_name]

    def set_random_numerical_descriptors(self) -> "DescriptorValueMap":
        """
        Assigns random values to all numerical descriptors and saves the model.

        Returns:
            dict: The updated numerical_descriptors dictionary with assigned random values.
        """
        if not self.subcategories or not self.numerical_descriptors:
            self.save()

        assert self.numerical_descriptors is not None, (
            "Numerical descriptors must be initialized."
        )

        numerical_descriptors = self.numerical_descriptors

        for (
            numerical_descriptor_name,
            _numerical_descriptor_dict,
        ) in numerical_descriptors.items():
            self.set_random_numerical_descriptor(numerical_descriptor_name, save=False)

        self.save()

        return self.numerical_descriptors
