from django.db import models

# Serializer located in serializers/examination.py
class PatientExamination(models.Model):
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='patient_examinations')
    examination = models.ForeignKey('Examination', on_delete=models.CASCADE, null = True, blank = True)
    video = models.OneToOneField('Video', on_delete=models.CASCADE, null = True, blank = True, related_name='patient_examination')
    report_file = models.OneToOneField('ReportFile', on_delete=models.CASCADE, null = True, blank = True, related_name='patient_examination')

    class Meta:
        verbose_name = 'Patient Examination'
        verbose_name_plural = 'Patient Examinations'
        ordering = ['patient', 'examination']

    def __str__(self):
        return f"{self.patient} - {self.report_file}"
    
    def find_matching_video_from_patient(self):
        """
        Finds a video for this patient examination based on the patient's videos.
        For this, the videos date must be the same as the report file's date.
        #TODO add more criteria for matching: Examination type
        """
        videos = self.patient.video_set.filter(date=self.report_file.date, patient_examination__isnull=True)
        if videos:
            if len(videos) > 1:
                print(f"Warning: Found more than one video for patient {self.patient} on date {self.report_file.date}. Choosing the first one.")
            return videos[0]
        else:
            videos = self.patient.video_set.filter(patient_examination__isnull=True)
            if len(videos)==1:
                return videos[0]
            
        return None

