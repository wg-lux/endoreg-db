from django.db import models
from typing import TYPE_CHECKING, Optional, List  # Added List

# Added imports for type hints
if TYPE_CHECKING:
    from ...administration.person.patient import Patient
    from ..medication import MedicationIndication, Medication, MedicationIntakeTime
    from ...other.unit import Unit
    from ....utils.links.requirement_link import RequirementLinks # Added RequirementLinks

class PatientMedication(models.Model):
    """
    Represents a specific medication prescribed or taken by a patient.

    Links a patient to a medication, its indication, dosage, intake times, and unit.
    """
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE)
    medication_indication = models.ForeignKey(
        "MedicationIndication", on_delete=models.CASCADE,
        related_name="indication_patient_medications", null=True
    )

    medication = models.ForeignKey(
        'Medication', on_delete=models.CASCADE,
        blank=True,
        related_name='medication_patient_medications'
    )

    intake_times = models.ManyToManyField(
        'MedicationIntakeTime', 
        related_name='intake_time_patient_medications',
        blank=True,
    )

    unit = models.ForeignKey(
        'Unit', on_delete=models.CASCADE,
        null=True, blank=True
    )
    dosage = models.JSONField(
        null=True, blank=True
    )
    active = models.BooleanField(default=True)

    objects = models.Manager()

    if TYPE_CHECKING:  # Added type hints block
        patient: "Patient"
        medication_indication: Optional["MedicationIndication"]
        medication: Optional["Medication"]
        intake_times: models.QuerySet["MedicationIntakeTime"]
        unit: Optional["Unit"]
        dosage: Optional[dict]

    @property
    def links(self) -> "RequirementLinks":
        """
        Returns a RequirementLinks object for this PatientMedication instance.
        This is used during requirement evaluation.
        """
        from ....utils.links.requirement_link import RequirementLinks
        
        meds: List["Medication"] = []
        if self.medication:
            meds.append(self.medication)
        
        indications: List["MedicationIndication"] = []
        if self.medication_indication:
            indications.append(self.medication_indication)

        intake_times_list: List["MedicationIntakeTime"] = list(self.intake_times.all())

        return RequirementLinks(
            medications=meds,
            medication_indications=indications,
            medication_intake_times=intake_times_list,
            patient_medications=[self] # Include self in patient_medications
        )

    class Meta:
        verbose_name = 'Patient Medication'
        verbose_name_plural = 'Patient Medications'

    @classmethod
    def create_by_patient_and_indication(cls, patient, medication_indication):
        """Creates a PatientMedication instance linking a patient and an indication."""
        from ..medication import MedicationIndication
        medication_indication: MedicationIndication
        patient_medication = cls.objects.create(patient=patient, medication_indication=medication_indication)
        patient_medication.save()
        
        return patient_medication


    def __str__(self):
        """Returns a string representation including medication, indication, dosage, and intake times."""
        intake_times = self.intake_times.all()
        out = f"{self.medication} (Indication {self.medication_indication}) - "
        out += f"{self.dosage} - {self.unit} - "

        for intake_time in intake_times:
            out += f"{intake_time} - "

        return out

