from typing import List

from django.db import models


# Serializer located in serializers/examination.py
class PatientExamination(models.Model):
    patient = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, related_name="patient_examinations"
    )
    examination = models.ForeignKey(
        "Examination", on_delete=models.CASCADE, null=True, blank=True
    )
    video = models.OneToOneField(
        "Video",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="patient_examination",
    )
    report_file = models.OneToOneField(
        "ReportFile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="patient_examination",
    )
    date_start = models.DateField(null=True, blank=True)
    date_end = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Patient Examination"
        verbose_name_plural = "Patient Examinations"
        ordering = ["patient", "examination", "date_start"]

    def __str__(self):
        return f"{self.patient} - {self.examination} - {self.date_start}"

    def get_patient_age_at_examination(self) -> int:
        """
        Returns the patient's age at the time of the examination.
        """

        from endoreg_db.models import Patient

        patient: Patient = self.patient
        dob = patient.get_dob()
        date_start = self.date_start
        return (date_start - dob).days // 365

    def get_available_findings(self):
        """
        Returns all findings that are associated with the examination of this patient examination.
        """
        from endoreg_db.models import Examination, Finding

        examination: Examination = self.examination
        findings: List[Finding] = [_ for _ in examination.get_available_findings()]
        return findings

    def get_findings(self):
        """
        Returns all findings that are associated with this patient examination.
        """
        from endoreg_db.models import PatientFinding

        patient_findings: List[PatientFinding] = [
            _ for _ in self.patient_findings.all()
        ]
        return patient_findings

    def get_indications(self):
        """
        Returns all indications that are associated with this patient examination.
        """
        from endoreg_db.models import PatientExaminationIndication

        indications: List[PatientExaminationIndication] = [
            _ for _ in self.indications.all()
        ]
        return indications

    def get_indication_choices(self):
        """
        Returns all indication choices that are associated with this patient examination.
        """
        from endoreg_db.models import ExaminationIndicationClassificationChoice

        choices: List[ExaminationIndicationClassificationChoice] = [
            _.indication_choice for _ in self.get_indications()
        ]
        return choices

    def create_finding(self, finding):
        """
        Adds a finding to this patient examination.
        """
        from endoreg_db.models import Examination, Finding, PatientFinding

        examination: Examination = self.examination
        assert examination

        finding: Finding

        patient_finding = PatientFinding.objects.create(
            patient_examination=self, finding=finding
        )

        patient_finding.save()

        return patient_finding

    def find_matching_video_from_patient(self):
        """
        Finds a video for this patient examination based on the patient's videos.
        For this, the videos date must be the same as the report file's date.
        #TODO add more criteria for matching: Examination type
        """
        videos = self.patient.video_set.filter(
            date=self.report_file.date, patient_examination__isnull=True
        )
        if videos:
            if len(videos) > 1:
                print(
                    f"Warning: Found more than one video for patient {self.patient} on date {self.report_file.date}. Choosing the first one."
                )
            return videos[0]
        else:
            videos = self.patient.video_set.filter(patient_examination__isnull=True)
            if len(videos) == 1:
                return videos[0]

        return None
