from django.db import models
from ..center import Center
import hashlib
from datetime import date, time

class ReportFile(models.Model):
    pdf = models.FileField(upload_to="raw_report_pdfs", blank=True, null=True)
    pdf_hash = models.CharField(max_length=255, unique=True)
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    meta = models.JSONField(blank=True, null=True)
    text = models.TextField(blank=True, null=True)
    text_anonymized = models.TextField(blank=True, null=True)
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE, blank=True, null=True)
    examiner = models.ForeignKey("Examiner", on_delete=models.CASCADE, blank=True, null=True)
    date = models.DateField(blank=True, null=True)
    time = models.TimeField(blank=True, null=True)

    def get_pdf_hash(self):
        #FIXME should use endoreg_db.utils.get_pdf_hash in future
        pdf = self.pdf
        pdf_hash = None

        if pdf:
            # Open the file in binary mode and read its contents
            with pdf.open(mode='rb') as f:
                pdf_contents = f.read()
                # Create a hash object using SHA-256 algorithm
                hash_object = hashlib.sha256(pdf_contents, usedforsecurity=False)
                # Get the hexadecimal representation of the hash
                pdf_hash = hash_object.hexdigest()
                assert len(pdf_hash) <= 255, "Hash length exceeds 255 characters"

        return pdf_hash

    def initialize_metadata_in_db(self, report_meta=None):
        if not report_meta:
            report_meta = self.meta
        self.set_examination_date_and_time(report_meta)
        self.patient, created = self.get_or_create_patient(report_meta)
        self.examiner, created = self.get_or_create_examiner(report_meta)
        self.save()    

    def get_or_create_patient(self, report_meta=None):
        from ..persons import Patient
        if not report_meta:
            report_meta = self.meta
        patient_first_name = report_meta['patient_first_name']
        patient_last_name = report_meta['patient_last_name']
        patient_dob = report_meta['patient_dob']

        patient, created = Patient.objects.get_or_create(
            first_name=patient_first_name,
            last_name=patient_last_name,
            dob=patient_dob
        )

        return patient, created
    
    def get_or_create_examiner(self, report_meta= None):
        from ..persons import Examiner
        if not report_meta:
            report_meta = self.meta
        examiner_first_name = report_meta['examiner_first_name']
        examiner_last_name = report_meta['examiner_last_name']
        examiner_center = self.center

        examiner, created = Examiner.objects.get_or_create(
            first_name=examiner_first_name,
            last_name=examiner_last_name,
            center=examiner_center
        )

        return examiner, created
        
    def set_examination_date_and_time(self, report_meta=None):
        if not report_meta:
            report_meta = self.meta
        examination_date_str = report_meta['examination_date']
        examination_time_str = report_meta['examination_time']

        if examination_date_str:
            # TODO: get django DateField compatible date from string (e.g. "2021-01-01")
            self.date = date.fromisoformat(examination_date_str)
        if examination_time_str:
            # TODO: get django TimeField compatible time from string (e.g. "12:00")
            self.time = time.fromisoformat(examination_time_str)
        


