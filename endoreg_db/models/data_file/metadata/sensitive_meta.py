from django.db import models

class SensitiveMeta(models.Model):
    examination_date = models.DateField(blank=True, null=True)
    patient_first_name = models.CharField(max_length=255, blank=True, null=True)
    patient_last_name = models.CharField(max_length=255, blank=True, null=True)
    patient_dob = models.DateField(blank=True, null=True)
    endoscope_type = models.CharField(max_length=255, blank=True, null=True)
    endoscope_sn = models.CharField(max_length=255, blank=True, null=True)

    @classmethod
    def create_from_dict(cls, data: dict):
        # data can contain more fields than the model has
        field_names = [_.name for _ in cls._meta.fields]
        selected_data = {k: v for k, v in data.items() if k in field_names}

        return cls.objects.create(**selected_data)
    
    def update_from_dict(self, data: dict):
        # data can contain more fields than the model has
        field_names = [_.name for _ in self._meta.fields]
        selected_data = {k: v for k, v in data.items() if k in field_names}

        for k, v in selected_data.items():
            setattr(self, k, v)
        
        self.save()
    
    def __str__(self):
        return f"SensitiveMeta: {self.examination_date} {self.patient_first_name} {self.patient_last_name} (*{self.patient_dob})"
    
