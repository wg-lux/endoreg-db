from django.db import models
from endoreg_db.utils.hashs import (
    get_patient_hash,
    get_patient_examination_hash,
    # get_hash_string,
)
from hashlib import sha256

# from datetime import date
from icecream import ic
import os

# get DJANGO_SALT from settings
SECRET_SALT = os.getenv("DJANGO_SALT", "default_salt")


class SensitiveMeta(models.Model):
    examination_date = models.DateField(blank=True, null=True)
    pseudo_patient = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    patient_first_name = models.CharField(max_length=255, blank=True, null=True)
    patient_last_name = models.CharField(max_length=255, blank=True, null=True)
    patient_dob = models.DateField(blank=True, null=True)
    pseudo_examination = models.ForeignKey(
        "PatientExamination",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
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

    examiner_first_name = models.CharField(max_length=255, blank=True, null=True)
    examiner_last_name = models.CharField(max_length=255, blank=True, null=True)

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
        from endoreg_db.models import Center, Examiner
        from endoreg_db.utils import guess_name_gender

        # data can contain more fields than the model has
        field_names = [_.name for _ in cls._meta.fields]
        selected_data = {k: v for k, v in data.items() if k in field_names}

        first_name = selected_data.get("patient_first_name")
        last_name = selected_data.get("patient_last_name")
        center_name = data.get("center_name")

        try:
            center = Center.objects.get_by_natural_key(center_name)
        except Exception as exc:
            raise ValueError(f"Center with name {center_name} does not exist") from exc

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

        sensitive_meta = cls.objects.create(**selected_data)
        sensitive_meta.get_or_create_pseudo_examiner()
        sensitive_meta.get_or_create_pseudo_patient()
        sensitive_meta.get_or_create_pseudo_patient_examination()

        ic("EXAMINER_FIRST_NAME", sensitive_meta.examiner_first_name)
        ic("EXAMINER_LAST_NAME", sensitive_meta.examiner_last_name)

        return sensitive_meta

    def get_or_create_pseudo_examiner(self):
        ic("GETTING OR CREATING EXAMINER")

        if self.examiners.exists():
            examiner = self.examiners.first()
            ic(f"Exisiting examiner: {examiner}")

        else:
            examiner = self.create_pseudo_examiner()
            ic(f"Created examiner: {examiner}")

        return examiner

    def create_pseudo_examiner(self):
        from endoreg_db.models import Examiner, Center

        first_name = self.examiner_first_name
        last_name = self.examiner_last_name
        center = self.center
        ic("CREATING EXAMINER", first_name, last_name, center)
        if not first_name or not last_name or not center:
            default_center = Center.objects.get_by_natural_key("endoreg_db_demo")
            examiner, _created = Examiner.custom_get_or_create(
                first_name="Unknown", last_name="Unknown", center=default_center
            )
        else:
            examiner, _created = Examiner.custom_get_or_create(
                first_name=first_name, last_name=last_name, center=center
            )
        self.examiners.add(examiner)
        self.save()

        return examiner

    def get_or_create_pseudo_patient(self):
        if not self.pseudo_patient:
            self.pseudo_patient = self.create_pseudo_patient()
            self.save()
        return self.pseudo_patient

    def create_pseudo_patient(self):
        from endoreg_db.models import Patient
        from datetime import date

        dob = self.patient_dob

        if isinstance(dob, str):
            dob = date.fromisoformat(dob)

        month = dob.month
        year = dob.year

        patient_hash = self.get_patient_hash()
        patient, _created = Patient.get_or_create_pseudo_patient_by_hash(
            patient_hash=patient_hash,
            center=self.center,
            gender=self.patient_gender,
            birth_year=year,
            birth_month=month,
        )

        return patient

    def get_or_create_pseudo_patient_examination(self):
        from endoreg_db.models import PatientExamination

        patient_hash = self.get_patient_hash()
        examination_hash = self.get_patient_examination_hash()

        patient_examination, _created = (
            PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
                patient_hash, examination_hash
            )
        )

        self.pseudo_examination = patient_examination

        return patient_examination

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
            SensitiveMeta._update_name_db(first_name=first_name, last_name=last_name)

        if not self.examination_hash:
            self.examination_hash = self.get_patient_examination_hash()
        if not self.patient_hash:
            self.patient_hash = self.get_patient_hash()

        examiner_first_name = data.get("examiner_first_name", "")
        examiner_last_name = data.get("examiner_last_name", "")

        if examiner_first_name and examiner_last_name:
            self.examiner_first_name = examiner_first_name
            self.examiner_last_name = examiner_last_name
        _examiner = self.get_or_create_pseudo_examiner()

        return self

    def __str__(self):
        result_str = "SensitiveMeta:"
        result_str += f"\tExamination Date: {self.examination_date}"
        result_str += f"\tFirst Name: {self.patient_first_name}"
        result_str += f"\tLast Name: {self.patient_last_name}"
        result_str += f"\tDate of Birth: (*{self.patient_dob})"
        result_str += f"\tGender: {self.patient_gender}"
        result_str += f"\tCenter: {self.center}"
        result_str += f"\tExaminers: {self.examiners.all()}"
        result_str += f"\tEndoscope Type: {self.endoscope_type}"
        result_str += f"\tEndoscope SN: {self.endoscope_sn}"
        result_str += f"\tState Verified: {self.state_verified}"
        result_str += f"\tPatient Hash: {self.patient_hash}"
        result_str += f"\tExamination Hash: {self.examination_hash}"

        return result_str

    def __repr__(self):
        return self.__str__()

    def get_patient_hash(self, salt=SECRET_SALT):
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

    def get_patient_examination_hash(self, salt=SECRET_SALT):
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

    # override save method to update hashes
    def save(self, *args, **kwargs):
        self.examination_hash = self.get_patient_examination_hash()
        self.patient_hash = self.get_patient_hash()
        self.pseudo_patient = self.create_pseudo_patient()
        self.pseudo_examination = self.get_or_create_pseudo_patient_examination()
        super().save(*args, **kwargs)

    @classmethod
    def _update_name_db(cls, first_name, last_name):
        from endoreg_db.models import FirstName, LastName

        FirstName.objects.get_or_create(name=first_name)
        LastName.objects.get_or_create(name=last_name)
