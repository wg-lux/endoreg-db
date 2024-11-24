from django.db import models


class PatientFinding(models.Model):
    patient_examination = models.ForeignKey('PatientExamination', on_delete=models.CASCADE, related_name='patient_findings')
    finding = models.ForeignKey('Finding', on_delete=models.CASCADE, related_name='patient_findings')
    location_choices = models.ManyToManyField(
        'PatientFindingLocation',
        blank=True,
        related_name='patient_findings'
    )
    morphology_choices = models.ManyToManyField(
        'PatientFindingMorphology',
        blank=True,
        related_name='patient_findings'
    )
    

    class Meta:
        verbose_name = 'Patient Finding'
        verbose_name_plural = 'Patient Findings'
        ordering = ['patient_examination', 'finding']

    def __str__(self):
        return f"{self.patient_examination} - {self.finding}"

   # functions to get all associated location and morphology choices
    def get_location_choices(self):
        """
        Returns all location choices that are associated with this patient finding.
        """
        from endoreg_db.models import PatientFindingLocation
        location_choices = [_ for _ in self.location_choices.all()]
        return location_choices
    
    def get_morphology_choices(self):
        """
        Returns all morphology choices that are associated with this patient finding.
        """
        from endoreg_db.models import PatientFindingMorphology
        morphology_choices = [_ for _ in self.morphology_choices.all()]
        return morphology_choices   

    def add_morphology_choice(self, morphology_choice, morphology_classification):
        """
        Adds a morphology choice to this patient finding morphology.
        """
        from endoreg_db.models import (
            FindingMorphologyClassificationChoice,
            FindingMorphologyClassification,
            PatientFindingMorphology
        )
        morphology_choice: FindingMorphologyClassificationChoice
        morphology_classification: FindingMorphologyClassification
        
        patient_finding_morphology = PatientFindingMorphology.objects.create(
            morphology_classification=morphology_classification,
            morphology_choice=morphology_choice
        )
        patient_finding_morphology.save()

        self.morphology_choices.add(patient_finding_morphology)
        self.save()

        return patient_finding_morphology

    def add_intervention(self, intervention, state="pending", date=None):
        """
        Adds an intervention to this patient finding.
        """
        from endoreg_db.models import PatientFindingIntervention
        patient_finding_intervention = PatientFindingIntervention.objects.create(
            patient_finding=self,
            intervention=intervention,
            state = state,
            date = date
        )
        patient_finding_intervention.save()

        return patient_finding_intervention

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