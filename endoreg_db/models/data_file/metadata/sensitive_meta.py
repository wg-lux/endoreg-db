from django.db import models
from endoreg_db.utils.hashs import (
    get_patient_hash,
    get_patient_examination_hash,
    get_hash_string,
)
from datetime import date
from icecream import ic


class SensitiveMeta(models.Model):
    examination_date = models.DateField(blank=True, null=True)
    patient_first_name = models.CharField(max_length=255, blank=True, null=True)
    patient_last_name = models.CharField(max_length=255, blank=True, null=True)
    patient_dob = models.DateField(blank=True, null=True)
    patient_gender = models.ForeignKey(
        "Gender",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    examiners = models.ManyToManyField(
        "Examiner",
        blank=True,
    )
    center = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )

    examination_hash = models.CharField(max_length=255, blank=True, null=True)
    patient_hash = models.CharField(max_length=255, blank=True, null=True)

    endoscope_type = models.CharField(max_length=255, blank=True, null=True)
    endoscope_sn = models.CharField(max_length=255, blank=True, null=True)
    state_verified = models.BooleanField(default=False)
    state_hash_generated = models.BooleanField(default=False)
    state_names_substituted = models.BooleanField(default=False)
    state_dob_substituted = models.BooleanField(default=False)
    state_examination_date_substituted = models.BooleanField(default=False)
    state_endoscope_sn_substituted = models.BooleanField(default=False)
    state_examiners_substituted = models.BooleanField(default=False)

    @classmethod
    def create_from_dict(cls, data: dict):
        from endoreg_db.models import Center
        from endoreg_db.utils import guess_name_gender

        # data can contain more fields than the model has
        field_names = [_.name for _ in cls._meta.fields]
        selected_data = {k: v for k, v in data.items() if k in field_names}

        first_name = selected_data.get("patient_first_name")
        last_name = selected_data.get("patient_last_name")
        center_name = data.get("center_name")

        try:
            center = Center.objects.get_by_natural_key(center_name)
        except Center.DoesNotExist:
            raise ValueError(f"Center with name {center_name} does not exist")
        selected_data["center"] = center

        try:
            # TODO Add to documentation and replace with better method
            gender = guess_name_gender(first_name)
        except:
            raise ValueError(f"Gender for name {first_name} could not be guessed")

        selected_data["patient_gender"] = gender

        if first_name and last_name:
            # TODO Add to documentation
            cls._update_name_db(first_name, last_name)

        return cls.objects.create(**selected_data)

    def get_or_create_pseudo_patient(self):
        from endoreg_db.models import Patient
        from datetime import date

        dob = self.patient_dob

        if isinstance(dob, str):
            dob = date.fromisoformat(dob)

        ic(type(dob))

        month = dob.month
        year = dob.year

        patient_hash = self.get_patient_hash()
        patient = Patient.get_or_create_pseudo_patient_by_hash(
            patient_hash=patient_hash,
            center=self.center,
            gender=self.patient_gender,
            birth_year=year,
            birth_month=month,
        )

        return patient

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

    def _get_hash_str_raw(
        self,
        first_name: str = "",
        last_name: str = "",
        dob_str: str = "",
        center_name: str = "",
        examination_date: date = date(1900, 1, 1),
        endoscope_sn: str = "",
        salt: str = "",
    ):
        # endoscope_sn = self.endoscope_sn
        endoscope_sn = ""  # TODO Do we want to include this?
        return get_hash_string(
            first_name=first_name,
            last_name=last_name,
            dob_str=dob_str,
            center_name=center_name,
            examination_date=examination_date,
            endoscope_sn=endoscope_sn,
            salt=salt,
        )

    def get_patient_hash(self, salt=""):
        from hashlib import sha256
        from datetime import datetime

        dob = self.patient_dob
        first_name = self.patient_first_name
        last_name = self.patient_last_name
        center = self.center

        assert dob, "Patient DOB is required"
        assert center, "Center is required"

        hash_str = get_patient_hash(
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            center=self.center.name,
            salt=salt,
        )
        return sha256(hash_str.encode()).hexdigest()

    def get_patient_examination_hash(self, salt=""):
        from hashlib import sha256
        from datetime import datetime

        dob = self.patient_dob
        first_name = self.patient_first_name
        last_name = self.patient_last_name
        examination_date = self.examination_date
        center = self.center

        assert dob, "Patient DOB is required"
        assert examination_date, "Examination date is required"

        hash_str = get_patient_examination_hash(
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            examination_date=examination_date,
            center=center.name,
            salt=salt,
        )

        return sha256(hash_str.encode()).hexdigest()

    @classmethod
    def _update_name_db(cls, first_name, last_name):
        from endoreg_db.models import FirstName, LastName

        FirstName.objects.get_or_create(name=first_name)
        LastName.objects.get_or_create(name=last_name)
