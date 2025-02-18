from django.db import models

class PatientFindingLocation(models.Model):
    location_classification = models.ForeignKey('FindingLocationClassification', on_delete=models.CASCADE, related_name='patient_finding_locations')
    location_choice = models.ForeignKey('FindingLocationClassificationChoice', on_delete=models.CASCADE, related_name='patient_finding_locations')
    subcategories = models.JSONField(blank=True, null=True)
    numerical_descriptors = models.JSONField(blank=True, null=True)

    class Meta:
        verbose_name = 'Patient Finding Location'
        verbose_name_plural = 'Patient Finding Locations'
        ordering = ['location_classification', 'location_choice']

    def __str__(self):
        return f"{self.location_classification} - {self.location_choice}"
    
    # override save method to do the following:
    # - check if location_choice is in location_classification.choices
    # - check if subcategories and numerical_descriptors exist
    # - if not set, fetch them from location_choice and set them

    def save(self, *args, **kwargs):
        if self.location_choice not in self.location_classification.choices.all():
            raise ValueError("location_choice must be in location_classification.choices")

        if not self.subcategories:
            self.subcategories = self.location_choice.subcategories

        if not self.numerical_descriptors:
            self.numerical_descriptors = self.location_choice.numerical_descriptors

        super().save(*args, **kwargs)

    def set_subcategory(self, subcategory_name, subcategory_value):
        """
        Sets a subcategory for this location.
        """
        assert subcategory_name in self.subcategories, "Subcategory must be in subcategories."
        self.subcategories[subcategory_name]["value"] = subcategory_value
        self.save()

        return self.subcategories[subcategory_name]

    def set_random_subcategories(self):
        """
        Sets random subcategories for this location if they are required.
        """
        import random
        if not self.subcategories or not self.numerical_descriptors:
            self.save()

        self.refresh_from_db()
        assert self.subcategories, "Subcategories must be set."

        subcategories = self.subcategories
        # print("SUBCATS")
        # print(subcategories)

        # subcategories is dict with keys as subcategory names and values as dict with keys as "choices" (List of str) and "required" (bool)
        # for each subcategory, set a random choice if it is required
        for subcategory_name, subcategory_dict in subcategories.items():
            if subcategory_dict["required"]:
                subcategory_choice = random.choice(subcategory_dict["choices"])
                self.subcategories[subcategory_name]["value"] = subcategory_choice

        self.save()

        return self.subcategories
    
    def set_random_numerical_descriptor(self, descriptor_name):
        """
        Sets a random numerical descriptor for this location.
        """
        import random
        if descriptor_name not in self.numerical_descriptors:
            raise ValueError("Descriptor name must be in numerical descriptors.")

        numerical_descriptor = self.numerical_descriptors[descriptor_name]
        min_value = numerical_descriptor["min"]
        max_value = numerical_descriptor["max"]

        assert min_value <= max_value, "Min value must be less than or equal to max value."

        random_value = random.uniform(min_value, max_value)
        self.numerical_descriptors[descriptor_name]["value"] = random_value
        self.save()

        return self.numerical_descriptors[descriptor_name]


    def set_random_numerical_descriptors(self):
        """
        Sets random numerical descriptors for this location if they are required.
        """
        import random
        from endoreg_db.models import FindingLocationClassificationChoice
        if not self.subcategories or not self.numerical_descriptors:
            self.save()

        numerical_descriptors = self.numerical_descriptors
        numerical_descriptor = {}

        # numerical_descriptors is dict with keys as numerical descriptor names 
        # and values as dict with keys as "min" (float), "max" (float), required (bool), distribution_name (str)
        # distribution name can be either "uniform" or "normal"
        # for each numerical descriptor, set a random value between min and max
        for numerical_descriptor_name, numerical_descriptor_dict in numerical_descriptors.items():
            min_value = numerical_descriptor_dict["min"]
            max_value = numerical_descriptor_dict["max"]

            assert min_value <= max_value, "Min value must be less than or equal to max value."


            random_value = random.uniform(min_value, max_value)
            numerical_descriptor[numerical_descriptor_name] = random_value

        self.numerical_descriptors = numerical_descriptor
        self.save()

        return numerical_descriptor
