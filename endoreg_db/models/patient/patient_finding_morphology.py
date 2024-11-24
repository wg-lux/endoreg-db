from django.db import models

class PatientFindingMorphology(models.Model):
    morphology_classification = models.ForeignKey(
        'FindingMorphologyClassification', on_delete=models.CASCADE, related_name='patient_finding_morphologies'
    ) 
    morphology_choice = models.ForeignKey(
        'FindingMorphologyClassificationChoice', on_delete=models.CASCADE, related_name='patient_finding_morphologies'
    )
    subcategories = models.JSONField(blank=True, null=True)
    numerical_descriptors = models.JSONField(blank=True, null=True)

    class Meta:
        verbose_name = 'Patient Finding Morphology'
        verbose_name_plural = 'Patient Finding Morphologies'
        ordering = ['morphology_classification', 'morphology_choice']

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

    def save(self, *args, **kwargs):
        if self.morphology_choice not in self.morphology_classification.choices.all():
            raise ValueError("morphology_choice must be in morphology_classification.choices")

        if not self.subcategories:
            self.subcategories = self.morphology_choice.subcategories
            if not self.subcategories:
                self.subcategories = {}

        if not self.numerical_descriptors:
            self.numerical_descriptors = self.morphology_choice.numerical_descriptors
            if not self.numerical_descriptors:
                self.numerical_descriptors = {}
                

        super().save(*args, **kwargs)

    def set_random_numerical_descriptors(self):
        """
        Sets random numerical descriptors for this patient finding morphology.
        """
        import numpy as np
        # get numerical descriptors from morphology_choice
        try:
            numerical_descriptors = self.morphology_choice.numerical_descriptors
            assert numerical_descriptors
        except:
            print(f"Numerical descriptors not found for {self.morphology_choice}")
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