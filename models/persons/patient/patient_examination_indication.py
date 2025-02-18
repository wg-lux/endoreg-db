from django.db import models

class PatientExaminationIndication(models.Model):
    '''A model to store the indication for a patient examination.'''
    patient_examination = models.ForeignKey('PatientExamination', on_delete=models.CASCADE, related_name='indications')
    examination_indication = models.ForeignKey('ExaminationIndication', on_delete=models.CASCADE)
    indication_choice = models.ForeignKey('ExaminationIndicationClassificationChoice', on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):
        return f"{self.patient_examination} - {self.examination_indication}"

    def get_examination(self):
        from endoreg_db.models import Examination
        pe = self.get_patient_examination()
        e: Examination = pe.examination

        return e
    
    def get_patient_examination(self):
        from endoreg_db.models import PatientExamination
        pe: PatientExamination = self.patient_examination
        return pe
    
    def get_choices(self):
        from endoreg_db.models import (
            ExaminationIndicationClassificationChoice,
            ExaminationIndication
        )
        examination_indication:ExaminationIndication = self.examination_indication
        choices = [_ for _ in examination_indication.get_choices()]
        return choices
    