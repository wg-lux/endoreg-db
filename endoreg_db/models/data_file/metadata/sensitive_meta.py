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

        first_name = selected_data.get("patient_first_name")
        last_name = selected_data.get("patient_last_name")

        if first_name and last_name:
            cls._update_name_db(first_name, last_name)

        return cls.objects.create(**selected_data)
    
    def update_from_dict(self, data: dict):
        # data can contain more fields than the model has
        field_names = [_.name for _ in self._meta.fields]
        selected_data = {k: v for k, v in data.items() if k in field_names}

        for k, v in selected_data.items():
            setattr(self, k, v)
        
        self.save()
        first_name = self.patient_first_name
        last_name = self.patient_last_name

        if first_name and last_name:
            SensitiveMeta._update_name_db()
    
        return self

    def __str__(self):
        return f"SensitiveMeta: {self.examination_date} {self.patient_first_name} {self.patient_last_name} (*{self.patient_dob})"
    
    def __repr__(self):
        return self.__str__()
    
    @classmethod
    def _update_name_db(cls, first_name, last_name):
        from endoreg_db.models import FirstName, LastName

        FirstName.objects.get_or_create(name=first_name)
        LastName.objects.get_or_create(name=last_name)

