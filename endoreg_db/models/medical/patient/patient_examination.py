from django.db import models
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING: 
    from ...administration.person.patient import Patient
    from ..finding import Finding
    from .patient_finding import PatientFinding
    from ..examination import Examination
    from ...media import VideoFile, RawPdfFile, AnonymExaminationReport, AnonymHistologyReport
    from .patient_examination_indication import PatientExaminationIndication
    from ..examination import ExaminationIndicationClassificationChoice

class PatientExamination(models.Model):
    patient = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, related_name="patient_examinations"
    )
    examination = models.ForeignKey(
        "Examination", on_delete=models.CASCADE, null=True, blank=True
    )
    video = models.OneToOneField(
        "VideoFile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="patient_examination",
    )
    date_start = models.DateField(null=True, blank=True)
    date_end = models.DateField(null=True, blank=True)
    hash = models.CharField(max_length=255, unique=True)

    if TYPE_CHECKING:
        patient: "Patient"
        examination: "Examination"
        video: "VideoFile"
        patient_findings: models.QuerySet["PatientFinding"]
        indications: models.QuerySet["PatientExaminationIndication"]
        raw_pdf_files: models.QuerySet["RawPdfFile"]
        anonymexaminationreport_set: models.QuerySet["AnonymExaminationReport"]
        anonymhistologyreport_set: models.QuerySet["AnonymHistologyReport"]

    # report_files
    class Meta:
        verbose_name = "Patient Examination"
        verbose_name_plural = "Patient Examinations"
        ordering = ["patient", "examination", "date_start"]

    @classmethod
    def get_or_create_pseudo_patient_examination_by_hash(
        cls,
        patient_hash: str,
        examination_hash: str,
        examination_name: Optional[str] = None,
    ):
        from ...administration.person import Patient
        from ..examination import Examination

        created = False

        if PatientExamination.objects.filter(
            patient__patient_hash=patient_hash, hash=examination_hash
        ).exists():
            return PatientExamination.objects.get(
                patient__patient_hash=patient_hash, hash=examination_hash
            ), created

        patient, created = Patient.get_or_create_pseudo_patient_by_hash(patient_hash)
        if examination_name is not None:
            examination = Examination.objects.get(name=examination_name)
        else:
            examination = None

        patient_examination = cls.objects.create(
            patient=patient, examination=examination, hash=examination_hash
        )

        patient_examination.save()

        created = True
        return patient_examination, created

    def __str__(self):
        return f"{self.patient} - {self.examination} - {self.date_start}"

    # override save method to make sure that the hash is always set,
    # if none is existing generate an unique string

    def generate_default_hash(self):
        # create random hash
        import random
        import string

        _hash = "DEFAULT_HASH_" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=10)
        )

        return _hash

    def save(self, *args, **kwargs):
        if not self.hash:
            self.hash = self.generate_default_hash()
        super().save(*args, **kwargs)

    def get_patient_age_at_examination(self) -> int:
        """
        Returns the patient's age at the time of the examination.
        """
        from ...administration.person.patient import Patient

        patient: Patient = self.patient
        dob = patient.get_dob()
        date_start = self.date_start
        return (date_start - dob).days // 365

    def get_available_findings(self):
        """
        Returns all findings that are associated with the examination of this patient examination.
        """

        return self.examination.get_available_findings()

    def get_findings(self) -> models.QuerySet["PatientFinding"]:
        """
        Returns all findings that are associated with this patient examination.
        """

        return self.patient_findings.all()

    def get_indications(self) -> models.QuerySet["PatientExaminationIndication"]:
        """
        Returns all indications that are associated with this patient examination.
        """
        return self.indications.all()

    def get_indication_choices(self) -> List["ExaminationIndicationClassificationChoice"]:
        """
        Returns all indication choices that are associated with this patient examination.
        """

        choices = [
            _.indication_choice for _ in self.get_indications()
        ]
        return choices

    def create_finding(self, finding:"Finding") -> "PatientFinding":
        """
        Adds a finding to this patient examination.
        """
        from .patient_finding import PatientFinding

        examination: Examination = self.examination
        assert examination

        patient_finding = PatientFinding.objects.create(
            patient_examination=self, finding=finding
        )

        patient_finding.save()

        return patient_finding
