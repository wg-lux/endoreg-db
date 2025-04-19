# from django.db import models

# class VideoImportMeta(models.Model):
#     processor = models.ForeignKey('EndoscopyProcessor', on_delete=models.CASCADE)
#     endoscope = models.ForeignKey('Endoscope', on_delete=models.CASCADE, blank=True, null=True)
#     center = models.ForeignKey('Center', on_delete=models.CASCADE)
#     video_anonymized = models.BooleanField(default=False)
#     video_patient_data_detected = models.BooleanField(default=False)
#     outside_detected = models.BooleanField(default=False)
#     patient_data_removed = models.BooleanField(default=False)
#     outside_removed = models.BooleanField(default=False)

#     def __str__(self):
#         result_html = ""

#         result_html += f"Processor: {self.processor.name}<br>"
#         result_html += f"Endoscope: {self.endoscope.name}<br>"
#         result_html += f"Center: {self.center.name}<br>"
#         result_html += f"Video anonymized: {self.video_anonymized}<br>"
#         result_html += f"Video patient data detected: {self.video_patient_data_detected}<br>"
#         result_html += f"Outside detected: {self.outside_detected}<br>"
#         result_html += f"Patient data removed: {self.patient_data_removed}<br>"
#         result_html += f"Outside removed: {self.outside_removed}<br>"
#         return result_html
    
