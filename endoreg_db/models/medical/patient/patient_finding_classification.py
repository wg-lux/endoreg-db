from django.db import models
from typing import TYPE_CHECKING, Dict
import random


# Corrected imports for type hints
if TYPE_CHECKING:
    from ..finding import (
        FindingClassification, 
        FindingClassificationChoice, 
    )
    from .patient_finding import PatientFinding

class PatientFindingClassification(models.Model):
    """Represents basic classifications for specific findings in a patient context.
    Links a PatientFinding to a specific classification and choice, with optional subcategory values.
    """
    finding = models.ForeignKey(
        "PatientFinding", 
        on_delete=models.CASCADE, 
        related_name="classifications"
    )
    classification = models.ForeignKey(
        "FindingClassification", 
        on_delete=models.CASCADE, 
        related_name="patient_finding_classifications"
    )
    classification_choice = models.ForeignKey(
        "FindingClassificationChoice", 
        on_delete=models.CASCADE, 
        related_name="patient_finding_classifications"
    )

    is_active = models.BooleanField(default=True, help_text="Indicates if the classification is currently active.")
    subcategories = models.JSONField(blank=True, null=True)
    numerical_descriptors = models.JSONField(blank=True, null=True)

    if TYPE_CHECKING:
        finding: "PatientFinding"
        classification: "FindingClassification"
        classification_choice: "FindingClassificationChoice"

    class Meta:
        verbose_name = 'Patient Finding Classification'
        verbose_name_plural = 'Patient Finding Classifications'
        ordering = ['finding', 'classification', 'classification_choice']

    def __str__(self):
        return f"{self.finding} - {self.classification} - {self.classification_choice}"

    def save(self, *args, **kwargs):
        """Override save method to ensure classification_choice is valid for the classification."""
        if self.classification_choice not in self.classification.choices.all():
            raise ValueError("classification_choice must be in classification.choices")

        if not self.subcategories:
            self.subcategories = self.classification_choice.subcategories

        if not self.numerical_descriptors:
            self.numerical_descriptors = self.classification_choice.numerical_descriptors

        super().save(*args, **kwargs)

    def get_subcategories(self):
        """Returns the dictionary of subcategories, ensuring it's initialized."""
        if not self.subcategories:
            self.save()
        return self.subcategories

    def get_numerical_descriptors(self):
        """Returns the dictionary of numerical descriptors, ensuring it's initialized."""
        if not self.numerical_descriptors:
            self.save()
        return self.numerical_descriptors

    def set_subcategory(self, subcategory_name: str, subcategory_value: Dict):
        """
        Sets a subcategory for this classification.
        """
        assert subcategory_name in self.subcategories, "Subcategory must be in subcategories."
        self.subcategories[subcategory_name]["value"] = subcategory_value
        self.save()

        return self.subcategories[subcategory_name]
    
    def set_random_subcategories(self):
        """
        Sets random subcategories for this classification.
        This method should be implemented to set random values for subcategories.
        """
        if not self.subcategories or not self.numerical_descriptors:
            self.save()
        
        self.refresh_from_db()

        for subcategory_name, subcategory_dict in self.subcategories.items():
            if subcategory_dict["required"] and not subcategory_dict.get("value", None):
                subcategory_choice = random.choice(subcategory_dict["choices"])
                self.subcategories[subcategory_name]["value"] = subcategory_choice

        self.save()

        return self.subcategories
    
    def get_random_value_for_numerical_descriptor(self, descriptor_name):
        """Generates a random value for a specified numerical descriptor based on its definition."""
        import numpy as np
        assert descriptor_name in self.numerical_descriptors, "Descriptor must be in numerical descriptors."
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

    def set_random_numerical_descriptor(self, descriptor_name, save=True):
        """
        Sets a random numerical descriptor for this classification.
        """
        if descriptor_name not in self.numerical_descriptors:
            raise ValueError("Descriptor name must be in numerical descriptors.")

        value = self.get_random_value_for_numerical_descriptor(descriptor_name)
        
        self.numerical_descriptors[descriptor_name]["value"] = value
        if save:
            self.save()

        return self.numerical_descriptors[descriptor_name]
    
    def set_random_numerical_descriptors(self):
        """
        Sets random numerical descriptors for this location if they are required.
        """
        if not self.subcategories or not self.numerical_descriptors:
            self.save()

        numerical_descriptors = self.numerical_descriptors

        for numerical_descriptor_name, _numerical_descriptor_dict in numerical_descriptors.items():
            self.set_random_numerical_descriptor(numerical_descriptor_name, save=False)

        self.save()

        return self.numerical_descriptors
