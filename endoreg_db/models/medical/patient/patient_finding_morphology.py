from django.db import models
from typing import TYPE_CHECKING, Dict

# Corrected imports for type hints
if TYPE_CHECKING:
    from ..finding import (
        FindingClassification,
        FindingClassificationChoice,
    )
    from .patient_finding import PatientFinding

class PatientFindingMorphology(models.Model):
    """
    Represents the morphological description of a specific patient finding.

    Links a PatientFinding to a specific choice within a morphology classification,
    and stores associated subcategory values and numerical descriptors.
    """
    finding = models.ForeignKey(
        'PatientFinding', on_delete=models.CASCADE, related_name='morphologies'
    )
    morphology_classification = models.ForeignKey(
        'FindingClassification', on_delete=models.CASCADE, related_name='patient_finding_morphologies'
    ) 
    morphology_choice = models.ForeignKey(
        'FindingClassificationChoice', on_delete=models.CASCADE, related_name='patient_finding_morphologies'
    )
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    if TYPE_CHECKING:
        patient_finding: "PatientFinding" # Corrected name
        morphology_classification: "FindingClassification" # Corrected type
        morphology_choice: "FindingClassificationChoice" # Corrected type
        subcategories: Dict[str, Dict[str, str]]  # Corrected type
        numerical_descriptors: Dict[str, Dict[str, str]]  # Corrected type

    class Meta:
        verbose_name = 'Patient Finding Morphology'
        verbose_name_plural = 'Patient Finding Morphologies'
        ordering = ['morphology_classification', 'morphology_choice']

    def __str__(self):
        """Returns a string representation including classification, choice, and any set values."""
        _str = f"{self.morphology_classification} - {self.morphology_choice}"
        
        if self.subcategories:
            for key, _dict in self.subcategories.items():
                value = _dict.get("value", None)
                if value:
                    _str += f" - {key}: {value}"

        if self.numerical_descriptors:
            for key, _dict in self.numerical_descriptors.items():
                value = _dict.get("value", None)
                if value:
                    _str += f" - {key}: {value}"
        
        return _str

    def set_subcategory(self, subcategory_name, subcategory_value):
        """Sets the value for a specific subcategory."""
        assert subcategory_name in self.subcategories, "Subcategory must be in subcategories."
        self.subcategories[subcategory_name]["value"] = subcategory_value
        self.save()

        return self.subcategories[subcategory_name]

    def save(self, *args, **kwargs):
        """
        Overrides save to validate choice and initialize subcategories/descriptors
        from the morphology choice if they are not already set.
        """
        if self.morphology_choice not in self.morphology_classification.choices.all():
            raise ValueError("morphology_choice must be in morphology_classification.choices")

        if not self.subcategories:
            self.subcategories = self.morphology_choice.subcategories or {}

        if not self.numerical_descriptors:
            self.numerical_descriptors = self.morphology_choice.numerical_descriptors or {}
                

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

    def set_numerical_descriptor_random(self, descriptor_name):
        """Sets a random value for a specified numerical descriptor."""
        assert descriptor_name in self.numerical_descriptors, "Descriptor must be in numerical descriptors."
        
        value = self.get_random_value_for_numerical_descriptor(descriptor_name)
        self.numerical_descriptors[descriptor_name]["value"] = value
        self.save()

        return self.numerical_descriptors[descriptor_name]

    def set_random_numerical_descriptors(self): #TODO Update
        """Sets random values for all required numerical descriptors based on their definitions."""
        import numpy as np
        # get numerical descriptors from morphology_choice
        try:
            numerical_descriptors = self.morphology_choice.numerical_descriptors
            assert numerical_descriptors
        except Exception as e:
            # print(f"Numerical descriptors not found for {self.morphology_choice}")
            return None
        # If available, numerical descriptors is a dict like this:
        # {
        #     "DESCRIPTOR_NAME": {
        #         "min": 0.5,
        #         "max": 1.5,
        #         "unit": "mm"
        #         "mean": 1.0
        #         "std": 0.1
        #         "required": True
        #     }
        #}
        # Iterate over all numerical descriptors
        # if required is true, set random values following the constraints and distribution
        # available distributions are: normal, uniform 
        
        for descriptor_name, descriptor in numerical_descriptors.items():
            required = descriptor.get("required", False)
            if required:
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
                
                self.numerical_descriptors[descriptor_name]["value"] = value

        self.save()