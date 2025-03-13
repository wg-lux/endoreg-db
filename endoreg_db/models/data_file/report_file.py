from datetime import date, time
from django.db import models
from .base_classes.abstract_pdf import AbstractPdfFile


class ReportFile(AbstractPdfFile):
    meta = models.JSONField(blank=True, null=True)
    text = models.TextField(blank=True, null=True)
    # sensitive_meta = models.ForeignKey(
    #     "SensitiveMeta",
    #     on_delete=models.SET_NULL,
    #     related_name="report_files",
    #     null=True,
    #     blank=True,
    # )
    # examination = models.ForeignKey(
    #     "PatientExamination",
    #     on_delete=models.SET_NULL,
    #     blank=True,
    #     null=True,
    #     related_name="report_files",
    # )
    # patient = models.ForeignKey(
    #     "Patient", on_delete=models.DO_NOTHING, blank=True, null=True
    # )
    # examiner = models.ForeignKey(
    #     "Examiner", on_delete=models.DO_NOTHING, blank=True, null=True
    # )
    date = models.DateField(blank=True, null=True)
    time = models.TimeField(blank=True, null=True)

    def get_or_create_examiner(self, examiner_first_name, examiner_last_name):
        from ..persons import Examiner

        examiner_center = self.center

        examiner, created = Examiner.objects.get_or_create(
            first_name=examiner_first_name,
            last_name=examiner_last_name,
            center=examiner_center,
        )

        return examiner, created

    def set_examination_date_and_time(self, report_meta=None):
        if not report_meta:
            report_meta = self.meta
        examination_date_str = report_meta["examination_date"]
        examination_time_str = report_meta["examination_time"]

        if examination_date_str:
            # TODO: get django DateField compatible date from string (e.g. "2021-01-01")
            self.date = date.fromisoformat(examination_date_str)
        if examination_time_str:
            # TODO: get django TimeField compatible time from string (e.g. "12:00")
            self.time = time.fromisoformat(examination_time_str)
