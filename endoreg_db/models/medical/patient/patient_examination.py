from typing import TYPE_CHECKING, List, Optional

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks

    from ...administration.person.patient import Patient
    from ...media import AnonymExaminationReport, AnonymHistologyReport, RawPdfFile, VideoFile
    from ..examination import Examination, ExaminationIndication, ExaminationIndicationClassificationChoice
    from ..finding import Finding
    from .patient_examination_indication import PatientExaminationIndication
    from .patient_finding import PatientFinding


class PatientExamination(models.Model):
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE, related_name="patient_examinations")
    examination = models.ForeignKey("Examination", on_delete=models.CASCADE, null=True, blank=True)
    video = models.OneToOneField(
        "VideoFile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="patient_examination",
    )

    objects = models.Manager()

    date_start = models.DateField(null=True, blank=True)
    date_end = models.DateField(null=True, blank=True)
    hash = models.CharField(max_length=255, unique=True)

    if TYPE_CHECKING:
        patient: "models.ForeignKey[Patient]"
        examination: "models.ForeignKey[Examination|None]"
        video: "models.OneToOneField[VideoFile|None]"
        patient_findings: models.QuerySet["PatientFinding"]
        indications: models.QuerySet["PatientExaminationIndication"]
        raw_pdf_files: models.QuerySet["RawPdfFile"]
        anonymexaminationreport_set: models.QuerySet["AnonymExaminationReport"]
        anonymhistologyreport_set: models.QuerySet["AnonymHistologyReport"]

    @property
    def examination_safe(self):
        if self.examination is None:
            raise ValueError("Examination is not set for this PatientExamination.")
        return self.examination

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

        if PatientExamination.objects.filter(patient__patient_hash=patient_hash, hash=examination_hash).exists():
            return PatientExamination.objects.get(patient__patient_hash=patient_hash, hash=examination_hash), created

        patient, created = Patient.get_or_create_pseudo_patient_by_hash(patient_hash)
        if examination_name is not None:
            examination = Examination.objects.get(name=examination_name)
        else:
            examination = None

        patient_examination = cls.objects.create(patient=patient, examination=examination, hash=examination_hash)

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

        _hash = "DEFAULT_HASH_" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))

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
        assert dob is not None
        assert date_start is not None
        return (date_start - dob).days // 365

    def get_available_findings(self):
        """
        Returns all findings that are associated with the examination of this patient examination.
        """

        assert self.examination is not None
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
        Returns a list of indication choices associated with this patient examination.

        Only includes indication choices that are not None.
        """

        choices = [_.indication_choice for _ in self.get_indications() if _.indication_choice is not None]
        return choices

    def get_or_create_patient_examination_by_id(self, pk: int) -> Optional["PatientExamination"]:
        """Hilfsmethode zum Abrufen oder Erstellen einer PatientExamination nach ID"""
        if not self.objects.filter(pk=pk).exists():
            return None
        else:
            return self.objects.filter(pk=pk).first()

    @property
    def links(self) -> "RequirementLinks":
        """
        Aggregates and returns all related model instances relevant for requirement evaluation
        as a RequirementLinks object.

        This includes:
        - All findings associated with this examination
        - All classifications and choices from those findings
        - All interventions from those findings
        - Examination indications and their choices
        - Patient lab values
        """
        from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
        from endoreg_db.utils.links.requirement_link import RequirementLinks

        # Get all PatientExaminationIndication instances linked to this PatientExamination
        patient_exam_indications = self.indications.all()

        examination_indications_list: List["ExaminationIndication"] = []
        indication_choices_list: List["ExaminationIndicationClassificationChoice"] = []

        for pei in patient_exam_indications:
            if pei.examination_indication:
                examination_indications_list.append(pei.examination_indication)
            if pei.indication_choice:
                indication_choices_list.append(pei.indication_choice)

        # Fetch all patient lab values associated with this patient examination's patient
        patient_lab_values = []
        if self.patient:
            patient_lab_values = list(PatientLabValue.objects.filter(patient=self.patient))

        current_examination = [self.examination] if self.examination else []

        # Now aggregate findings data from all PatientFinding instances
        findings_list = []
        finding_classifications_list = []
        finding_classification_choices_list = []
        finding_interventions_list = []
        patient_findings_list = []

        for patient_finding in self.patient_findings.all():
            # Add the PatientFinding itself
            patient_findings_list.append(patient_finding)

            # Add the base Finding
            if patient_finding.finding:
                findings_list.append(patient_finding.finding)

            # Add all active classifications and their choices from this PatientFinding
            for pf_classification in patient_finding.active_classifications:
                if pf_classification.classification:
                    finding_classifications_list.append(pf_classification.classification)
                if pf_classification.classification_choice:
                    finding_classification_choices_list.append(pf_classification.classification_choice)

            # Add all active interventions from this PatientFinding
            for pf_intervention in patient_finding.active_interventions:
                if pf_intervention.intervention:
                    finding_interventions_list.append(pf_intervention.intervention)

        return RequirementLinks(
            patient_examinations=[self],  # Add the instance itself
            examinations=current_examination,  # Add the related Examination model
            examination_indications=examination_indications_list,
            examination_indication_classification_choices=indication_choices_list,
            patient_lab_values=patient_lab_values,
            # Add findings-related data
            patient_findings=patient_findings_list,
            findings=findings_list,
            finding_classifications=finding_classifications_list,
            finding_classification_choices=finding_classification_choices_list,
            finding_interventions=finding_interventions_list,
        )

    def create_finding(self, finding: "Finding") -> "PatientFinding":
        """
        Adds a finding to this patient examination.
        """
        from .patient_finding import PatientFinding

        examination = self.examination
        assert examination is not None

        patient_finding = PatientFinding.objects.create(patient_examination=self, finding=finding)

        patient_finding.save()

        return patient_finding
