from django.db import models


class PatientFinding(models.Model):
    patient_examination = models.ForeignKey('PatientExamination', on_delete=models.CASCADE, related_name='patient_findings')
    finding = models.ForeignKey('Finding', on_delete=models.CASCADE, related_name='patient_findings')
    location_choices = models.ManyToManyField(
        'PatientFindingLocation',
        blank=True,
        related_name='patient_findings'
    )
    #TODO morphology_choices

    class Meta:
        verbose_name = 'Patient Finding'
        verbose_name_plural = 'Patient Findings'
        ordering = ['patient_examination', 'finding']

    def __str__(self):
        return f"{self.patient_examination} - {self.finding}"

    def add_location_choice(self, location_choice, location_classification):
        """
        Adds a location choice to this patient finding location.
        """
        from endoreg_db.models import (
            FindingLocationClassificationChoice,
            FindingLocationClassification,
            PatientFindingLocation
        )
        location_choice: FindingLocationClassificationChoice
        location_classification: FindingLocationClassification
        
        patient_finding_location = PatientFindingLocation.objects.create(
            location_classification=location_classification,
            location_choice=location_choice
        )
        patient_finding_location.save()

        self.location_choices.add(patient_finding_location)
        self.save()

        return patient_finding_location


    def set_random_location(
            self, location_classification
        ):
        """
        Sets a random location for this finding based on the location classification.
        """
        from endoreg_db.models import (
            FindingLocationClassificationChoice,
            FindingLocationClassification, 
            PatientFindingLocation
        )
        import random
        from typing import List
        location_classification:FindingLocationClassification

        # assert location_classification in self.finding.location_classifications.all()

        location_choices:List[FindingLocationClassificationChoice] = location_classification.choices.all()
        location_choice = random.choice(location_choices)

        patient_finding_location = PatientFindingLocation.objects.create(
            location_classification=location_classification,
            location_choice=location_choice
        )

        self.location_choices.add(patient_finding_location)
        self.save()

        return patient_finding_location