from django.db import models

class PatientLabValue(models.Model):
    """
    A class representing a patient lab value.

    Attributes:
        patient (Patient): The patient.
        lab_value (LabValue): The lab value.
        value (float): The value of the lab value.
        date (datetime): The date of the lab value.
    """
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE,
                                related_name="lab_values", blank=True, null=True
    )
    lab_value = models.ForeignKey('LabValue', on_delete=models.CASCADE)
    value = models.FloatField(blank=True, null=True)
    value_str = models.CharField(max_length=255, blank=True, null=True)
    sample = models.ForeignKey(
        'PatientLabSample', on_delete=models.CASCADE, 
        blank=True, null=True,
        related_name='values'
    )
    datetime = models.DateTimeField(# if not set, use now
        auto_now_add=True        
    )
    normal_range = models.JSONField(
        default = dict
    )
    unit = models.ForeignKey('Unit', on_delete=models.CASCADE, blank=True, null=True)

    @classmethod
    def create_lab_value_by_sample(cls, sample=None, lab_value_name=None, value=None, value_str=None, unit=None):
        from endoreg_db.models import LabValue
        patient = sample.patient
        lab_value = LabValue.objects.get(name=lab_value_name)
        value = value
        value_str = value_str

        pat_lab_val = cls.objects.create(
            patient = patient,
            lab_value = lab_value,
            value = value,
            value_str = value_str,
            sample = sample,
            unit = unit,
        )

        pat_lab_val.save()

        return pat_lab_val

    def __str__(self):
        _str = f'{self.lab_value} - {self.value} {self.unit} ({self.datetime})'
        print(_str)
        return _str
    
    def set_min_norm_value(self, value, save = True):
        self.normal_range["min"] = value
        if save:
            self.save()

    def set_max_norm_value(self, value, save = True):
        self.normal_range["max"] = value
        if save:
            self.save()

    def set_norm_values_from_default(self):
        age = self.patient.age()
        gender = self.patient.gender
        min_value, max_value = self.lab_value.get_normal_range(age=age, gender=gender)
        self.set_min_norm_value(min_value, save = False)
        self.set_max_norm_value(max_value, save = False)
        self.save()


    def set_unit_from_default(self):
        self.unit = self.lab_value.default_unit
        self.save()

    def get_value(self):
        if self.value:
            return self.value
        else:
            return self.value_str
        
    def get_value_field_name(self):
        if self.value:
            return "value"
        else:
            return "value_str"
        
    # customize save method so that if a numeric value exists, we round it to the precision of the lab value
    def save(self, *args, **kwargs):
        if self.value:
            precision = self.lab_value.numeric_precision
            self.value = round(self.value, precision)
        super().save(*args, **kwargs)

    def set_value_by_distribution(self, distribution=None, save = True):
        from endoreg_db.models import (
            Patient, LabValue, Gender,
            DateValueDistribution,
            SingleCategoricalValueDistribution,
            NumericValueDistribution,
            MultipleCategoricalValueDistribution,
        ) 
        import warnings  

        patient:Patient = self.patient

        dob = patient.dob
        gender:Gender = patient.gender
        lab_value:LabValue = self.lab_value

        assert self.lab_value, "Lab value must be set to set value by distribution"
        self.unit = self.lab_value.default_unit

        if not distribution:
            distribution = lab_value.get_default_default_distribution()

            if not distribution:
                warnings.warn(
                    "No distribution set for lab value, assuming uniform numeric distribution based on normal values"
                )

                if not self.normal_range.get("min", None) or not self.normal_range.get("max", None):
                    self.set_norm_values_from_default()

                self.normal_range:dict
                _min = self.normal_range.get("min", 0.0001)
                _max = self.normal_range.get("max", 100)
                _name = "auto-" + self.lab_value.name + "-distribution-default-uniform" 
                distribution = NumericValueDistribution(
                    name = _name,
                    min_value = _min,
                    max_value = _max,
                    distribution_type = "uniform"
                )

                value = distribution.generate_value()
                self.value = value
                if save:
                    self.save()
                
                return value

        if isinstance(distribution, SingleCategoricalValueDistribution):
            value = distribution.generate_value()
            self.value_str = value
            if save:
                self.save()
            return value
        
        elif isinstance(distribution, NumericValueDistribution):
            value = distribution.generate_value()
            self.value = value
            if save:
                self.save()
            return value
        
        elif isinstance(distribution, MultipleCategoricalValueDistribution):
            value = distribution.generate_value()
            self.value_str = value
            if save:
                self.save()
            return value

        elif isinstance(distribution, DateValueDistribution):
            # raise not implemented error
            value = distribution.generate_value()
            self.value = value
            if save:
                self.save()


