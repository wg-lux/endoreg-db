from django.db import models


class PatientFindingMorphology(models.Model):
    morphology_classification = models.ForeignKey(
        "FindingMorphologyClassification",
        on_delete=models.CASCADE,
        related_name="patient_finding_morphologies",
    )
    morphology_choice = models.ForeignKey(
        "FindingMorphologyClassificationChoice",
        on_delete=models.CASCADE,
        related_name="patient_finding_morphologies",
    )
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Patient Finding Morphology"
        verbose_name_plural = "Patient Finding Morphologies"
        ordering = ["morphology_classification", "morphology_choice"]

    def __str__(self):
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

    # override save method to do the following:
    # - check if morphology_choice is in morphology_classification.choices
    # - check if subcategories and numerical_descriptors exist
    # - if not set, fetch them from morphology_choice and set them

    def set_subcategory(self, subcategory_name, subcategory_value):
        """
        Sets a subcategory for this morphology.
        """
        assert (
            subcategory_name in self.subcategories
        ), "Subcategory must be in subcategories."
        self.subcategories[subcategory_name]["value"] = subcategory_value
        self.save()

        return self.subcategories[subcategory_name]

    def save(self, *args, **kwargs):
        if self.morphology_choice not in self.morphology_classification.choices.all():
            raise ValueError(
                "morphology_choice must be in morphology_classification.choices"
            )

        if not self.subcategories:
            self.subcategories = self.morphology_choice.subcategories
            if not self.subcategories:
                self.subcategories = {}

        if not self.numerical_descriptors:
            self.numerical_descriptors = self.morphology_choice.numerical_descriptors
            if not self.numerical_descriptors:
                self.numerical_descriptors = {}

        super().save(*args, **kwargs)

    def get_subcategories(self):
        """
        Returns all subcategories that are associated with this patient finding morphology.
        """
        if not self.subcategories:
            self.save()
        return self.subcategories

    def get_numerical_descriptors(self):
        """
        Returns all numerical descriptors that are associated with this patient finding morphology.
        """
        if not self.numerical_descriptors:
            self.save()
        return self.numerical_descriptors

    def get_random_value_for_numerical_descriptor(self, descriptor_name):
        """
        Returns a random value for a numerical descriptor.
        """
        import numpy as np

        assert (
            descriptor_name in self.numerical_descriptors
        ), "Descriptor must be in numerical descriptors."
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
        """
        Sets a numerical descriptor for this patient finding morphology.
        """
        assert (
            descriptor_name in self.numerical_descriptors
        ), "Descriptor must be in numerical descriptors."

        value = self.get_random_value_for_numerical_descriptor(descriptor_name)
        self.numerical_descriptors[descriptor_name]["value"] = value
        self.save()

        return self.numerical_descriptors[descriptor_name]

    def set_random_numerical_descriptors(self):
        """
        Sets random numerical descriptors for this patient finding morphology.
        """
        try:
            numerical_descriptors = self.morphology_choice.numerical_descriptors
            if not numerical_descriptors:
                raise ValueError(
                    "Numerical descriptors not found for morphology choice."
                )
        except AttributeError as e:
            raise AttributeError(f"Error accessing numerical descriptors: {e}")

        for descriptor_name, descriptor in numerical_descriptors.items():
            required = descriptor.get("required", False)
            if required:
                try:
                    min_val = descriptor.get("min", 0)
                    max_val = descriptor.get("max", 1)
                    distribution = descriptor.get("distribution", "normal")

                    if distribution == "normal":
                        mean = descriptor.get("mean", 0.5)
                        std = descriptor.get("std", 0.1)
                        value = np.random.normal(mean, std)
                        value = np.clip(value, min_val, max_val)  # clip to min and max
                    elif distribution == "uniform":
                        value = np.random.uniform(min_val, max_val)
                    else:
                        raise ValueError(f"Unsupported distribution: {distribution}")

                    self.numerical_descriptors[descriptor_name]["value"] = value
                except KeyError as e:
                    raise KeyError(
                        f"Missing key in descriptor '{descriptor_name}': {e}"
                    )
                except ValueError as e:
                    raise ValueError(
                        f"Value error in descriptor '{descriptor_name}': {e}"
                    )
        self.save()
