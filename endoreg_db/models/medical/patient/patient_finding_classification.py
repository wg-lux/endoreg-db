from __future__ import annotations

import random
from typing import TYPE_CHECKING, cast, Any

import numpy as np
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.patient_finding_classification_runtime import (
    PatientFindingClassificationNumericalDescriptorPayload,
)

from endoreg_db.schemas import (
    build_patient_finding_numerical_descriptors,
    build_patient_finding_subcategories,
    validate_patient_finding_numerical_descriptors,
    validate_patient_finding_subcategories,
)

if TYPE_CHECKING:
    from endoreg_db.models import (
        FindingClassification,
        FindingClassificationChoice,
        PatientFinding,
    )
    from lx_dtypes.models.contracts.patient_finding_classification_runtime import (
        PatientFindingClassificationNumericalDescriptorsData,
        PatientFindingClassificationSubcategoriesData,
    )
    from lx_dtypes.models.contracts.finding_classification import (
        PatientFindingClassificationCore,
    )


class PatientFindingClassification(models.Model):
    """Represents basic classifications for specific findings in a patient context.
    Links a PatientFinding to a specific classification and choice, with optional subcategory values.
    """

    finding: models.ForeignKey["PatientFinding"] = models.ForeignKey(
        "PatientFinding", on_delete=models.CASCADE, related_name="classifications"
    )
    classification: models.ForeignKey["FindingClassification"] = models.ForeignKey(
        "FindingClassification",
        on_delete=models.CASCADE,
        related_name="patient_finding_classifications",
    )
    classification_choice: models.ForeignKey["FindingClassificationChoice"] = (
        models.ForeignKey(
            "FindingClassificationChoice",
            on_delete=models.CASCADE,
            related_name="patient_finding_classifications",
        )
    )

    is_active: models.BooleanField[Any, Any] = models.BooleanField(
        default=True, help_text="Indicates if the classification is currently active."
    )
    subcategories: models.JSONField[
        PatientFindingClassificationSubcategoriesData | None
    ] = models.JSONField(
        blank=True,
        null=True,
        default=dict,
    )
    numerical_descriptors: models.JSONField[
        PatientFindingClassificationNumericalDescriptorsData | None
    ] = models.JSONField(
        blank=True,
        null=True,
        default=dict,
    )

    if TYPE_CHECKING:

        @property
        def contract(self) -> PatientFindingClassificationCore: ...

    class Meta:
        verbose_name = "Patient Finding Classification"
        verbose_name_plural = "Patient Finding Classifications"
        ordering = ["finding", "classification", "classification_choice"]

    def __str__(self) -> str:
        """
        Return a string representation combining the finding, classification, and classification choice.
        """
        return f"{self.finding} - {self.classification} - {self.classification_choice}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.classification_choice not in self.classification.choices.all():
            errors["classification_choice"] = (
                "classification_choice must be in classification.choices"
            )

        if not self.subcategories:
            self.subcategories = build_patient_finding_subcategories(
                self.classification_choice.subcategories
            )
        if not self.numerical_descriptors:
            self.numerical_descriptors = build_patient_finding_numerical_descriptors(
                self.classification_choice.numerical_descriptors
            )

        for field_name, validator in (
            ("subcategories", validate_patient_finding_subcategories),
            ("numerical_descriptors", validate_patient_finding_numerical_descriptors),
        ):
            try:
                setattr(self, field_name, validator(getattr(self, field_name)))
            except ValueError as exc:
                errors[field_name] = str(exc)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: object, **kwargs: object) -> None:
        """
        Saves the model instance after validating and initializing classification-related fields.

        Ensures that the selected classification choice is valid for the associated classification. If subcategories or numerical descriptors are unset, initializes them from the classification choice before saving.
        """
        self.clean()
        super().save(*args, **kwargs)

    def initialize_and_get_subcategories(
        self,
    ) -> PatientFindingClassificationSubcategoriesData:
        """
        Ensure the subcategories field is initialized and return its dictionary.

        Returns:
            dict: The subcategories associated with this classification.
        """
        assert self.subcategories is not None
        return self.subcategories

    def initialize_and_get_descriptors(
        self,
    ) -> PatientFindingClassificationNumericalDescriptorsData:
        """
        Return the numerical descriptors dictionary, initializing it if necessary.

        If the `numerical_descriptors` field is empty or uninitialized, the method triggers model initialization and returns the resulting dictionary.
        """
        assert self.numerical_descriptors is not None
        return self.numerical_descriptors

    def set_subcategory(
        self, subcategory_name: str, subcategory_value: dict[str, object]
    ) -> dict[str, object]:
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

    def set_random_subcategories(self) -> PatientFindingClassificationSubcategoriesData:
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
            if subcategory_dict["required"] and not subcategory_dict.get("value"):
                subcategory_choice = random.choice(
                    cast(list[str], subcategory_dict["choices"])
                )
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
        descriptor_payload = (
            PatientFindingClassificationNumericalDescriptorPayload.model_validate(
                descriptor
            )
        )
        min_val = descriptor_payload.min
        max_val = descriptor_payload.max
        distribution = descriptor_payload.distribution
        if distribution == "normal":
            mean = descriptor_payload.mean
            std = descriptor_payload.std
            value = np.random.normal(mean, std)
            # clip value to min and max
            value = np.clip(value, min_val, max_val)
        elif distribution == "uniform":
            value = np.random.uniform(min_val, max_val)
        else:
            raise ValueError("Distribution not supported")

        return float(value)

    def set_random_numerical_descriptor(
        self, descriptor_name: str, save: bool = True
    ) -> dict[str, object]:
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

    def set_random_numerical_descriptors(
        self,
    ) -> PatientFindingClassificationNumericalDescriptorsData:
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
