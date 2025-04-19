from django.db import models
from endoreg_db.utils.hashs import (
    get_patient_hash,
    get_patient_examination_hash,
    # get_hash_string,
)
from hashlib import sha256
from django.utils import timezone
from datetime import datetime, timedelta, date
# from datetime import date
from icecream import ic
import os
import random
# get DJANGO_SALT from settings
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from endoreg_db.models import (
        Patient,
        PatientExamination,
        Gender,
        Examiner,
        Center,
        SensitiveMetaState, # Added import
    )
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
    patient_dob = models.DateTimeField(
        blank=True,
        null=True,
    )
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
    
    if TYPE_CHECKING:
        examiners: models.QuerySet["Examiner"]
        pseudo_patient: "Patient"
        patient_gender: "Gender"
        pseudo_examination: "PatientExamination"
        state: "SensitiveMetaState"
        center: "Center"

    @staticmethod
    def _generate_random_dob():
        """Generates a random timezone-aware datetime between 1920-01-01 and 2000-12-31."""
        start_date = date(1920, 1, 1)
        end_date = date(2000, 12, 31)
        time_between_dates = end_date - start_date
        days_between_dates = time_between_dates.days
        random_number_of_days = random.randrange(days_between_dates)
        random_date = start_date + timedelta(days=random_number_of_days)
        # Convert date to datetime at the beginning of the day
        random_datetime = datetime.combine(random_date, datetime.min.time())
        # Make it timezone aware using Django's current timezone
        return timezone.make_aware(random_datetime)

    @staticmethod
    def _generate_random_examination_date():
        """Generates a random date within the last 20 years."""
        today = date.today()
        start_date = today - timedelta(days=20*365) # Approximate 20 years back
        time_between_dates = today - start_date
        days_between_dates = time_between_dates.days
        random_number_of_days = random.randrange(days_between_dates)
        random_date = start_date + timedelta(days=random_number_of_days)
        return random_date

    @classmethod
    def create_from_dict(cls, data: dict):
        from endoreg_db.models import Center, Examiner
        from endoreg_db.utils import guess_name_gender

        default_name = "unknown"

        # data can contain more fields than the model has
        field_names = [_.name for _ in cls._meta.fields]
        selected_data = {k: v for k, v in data.items() if k in field_names}

        first_name = selected_data.get("patient_first_name", default_name)
        if not first_name:
            # If no first name is provided, use the default name
            first_name = default_name
        last_name = selected_data.get("patient_last_name", default_name)
        if not last_name:
            # If no last name is provided, use the default name
            last_name = default_name
        center_name = data.get("center_name")
        assert center_name

        try:
            center = Center.objects.get_by_natural_key(center_name)
        except Exception as exc:
            raise ValueError(f"Center with name {center_name} does not exist") from exc

        selected_data["center"] = center

        gender = guess_name_gender(first_name)
        assert gender

        selected_data["patient_gender"] = gender

        if first_name and last_name:
            # TODO Add to documentation
            cls._update_name_db(first_name, last_name)

        # Instantiate without saving yet
        sensitive_meta = cls(**selected_data)

        # The save method will now handle examiner linking.

        # Call save once at the end. This triggers our custom save logic.
        sensitive_meta.save()

        return sensitive_meta

    def get_or_create_pseudo_examiner(self):
        # Check if examiners already exist *after* potential save
        if self.pk and self.examiners.exists():
            examiner = self.examiners.first()
        else:
            # Create examiner instance but don't add yet if self.pk is None
            examiner = self.create_pseudo_examiner()

        return examiner

    def create_pseudo_examiner(self):
        from endoreg_db.models import Examiner, Center

        first_name = self.examiner_first_name
        last_name = self.examiner_last_name
        center = self.center
        if not first_name or not last_name or not center:
            default_center = Center.objects.get_by_natural_key("endoreg_db_demo")
            examiner, _created = Examiner.custom_get_or_create(
                first_name="Unknown", last_name="Unknown", center=default_center
            )
        else:
            examiner, _created = Examiner.custom_get_or_create(
                first_name=first_name, last_name=last_name, center=center
            )

        return examiner

    def get_or_create_pseudo_patient(self):
        if self.pseudo_patient_id:
            return self.pseudo_patient

        self.pseudo_patient = self.create_pseudo_patient()
        return self.pseudo_patient

    def create_pseudo_patient(self):
        from endoreg_db.models import Patient

        dob = self.patient_dob
        assert dob is not None, "Patient DOB should have been set by save() method"
        assert isinstance(dob, datetime), f"Expected datetime for dob, got {type(dob)}"

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

        if self.pseudo_examination_id:
            return self.pseudo_examination

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
        field_names = [_.name for _ in self._meta.fields]
        selected_data = {k: v for k, v in data.items() if k in field_names}

        # Set examiner names if provided, before calling save
        examiner_first_name = data.get("examiner_first_name", "")
        examiner_last_name = data.get("examiner_last_name", "")
        if examiner_first_name and examiner_last_name:
            self.examiner_first_name = examiner_first_name
            self.examiner_last_name = examiner_last_name

        # Update attributes
        for k, v in selected_data.items():
            # Avoid overwriting examiner names if they were just set
            if k not in ["examiner_first_name", "examiner_last_name"] or not (examiner_first_name and examiner_last_name):
                 setattr(self, k, v)

        # Call save - this will now handle DOB, hashes, pseudo patient/exam, AND examiner linking
        self.save()

        # Update name DB if needed
        first_name = self.patient_first_name
        last_name = self.patient_last_name
        if first_name and last_name:
            SensitiveMeta._update_name_db(first_name=first_name, last_name=last_name)

        return self

    def __str__(self):
        result_str = "SensitiveMeta:"
        result_str += f"\tExamination Date: {self.examination_date}"
        result_str += f"\tFirst Name: {self.patient_first_name}"
        result_str += f"\tLast Name: {self.patient_last_name}"
        result_str += f"\tDate of Birth: (*{self.patient_dob})"
        result_str += f"\tGender: {self.patient_gender}"
        result_str += f"\tCenter: {self.center}"
        # Check if instance has a pk before accessing ManyToMany field
        if self.pk:
            try:
                examiners_str = ", ".join([str(e) for e in self.examiners.all()])
                result_str += f"\tExaminers: [{examiners_str}]"
            except Exception: # Catch potential errors during M2M access
                 result_str += f"\tExaminers: [Error accessing examiners]"
        else:
            result_str += f"\tExaminers: [Not saved yet]"
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

    def save(self, *args, **kwargs):
        from endoreg_db.models import SensitiveMetaState # Import here to avoid circular dependency issues at module level

        # Ensure DOB exists before calculating hashes or creating related objects
        if not self.patient_dob:
            ic("Patient DOB is missing, generating a random one.")
            self.patient_dob = self._generate_random_dob()
            ic(f"Generated random DOB: {self.patient_dob}")

        # Ensure Examination Date exists before calculating hashes
        if not self.examination_date:
            ic("Examination date is missing, generating a random one.")
            self.examination_date = self._generate_random_examination_date()
            ic(f"Generated random examination date: {self.examination_date}")

        # Calculate hashes
        self.examination_hash = self.get_patient_examination_hash()
        self.patient_hash = self.get_patient_hash()

        # Ensure pseudo patient and examination FKs are set *before* initial save
        self.get_or_create_pseudo_patient()
        self.get_or_create_pseudo_patient_examination()

        # Determine if this is an insert or update
        force_insert = kwargs.get('force_insert', False)
        force_update = kwargs.get('force_update', False)
        is_new = self.pk is None

        # Save the main instance first to get a PK, exclude M2M fields for now
        # Temporarily remove 'examiners' from kwargs if present, as it's handled separately
        m2m_kwargs = {}
        if 'examiners' in kwargs:
            m2m_kwargs['examiners'] = kwargs.pop('examiners')

        super().save(*args, **kwargs) # Save non-M2M fields

        # Ensure a SensitiveMetaState exists after saving the main instance
        # Use related name 'state' if defined, otherwise access via SensitiveMetaState manager
        try:
            # Check if the related state object already exists
            _ = self.state
        except SensitiveMetaState.DoesNotExist:
            # If not, create a new one
            SensitiveMetaState.objects.create(origin=self)


        # Now handle the ManyToMany relationship (examiners)
        # Get or create the examiner instance
        examiner_instance = self.get_or_create_pseudo_examiner()
        # Add the examiner if it's not already associated
        if examiner_instance and not self.examiners.filter(pk=examiner_instance.pk).exists():
             self.examiners.add(examiner_instance)
             # No need for a second super().save() here, adding to M2M handles its own db interaction.

        # If 'examiners' was originally in kwargs, handle it (though our current logic overrides this)
        if 'examiners' in m2m_kwargs:
             # This part might need refinement depending on how 'examiners' is passed in kwargs
             pass

    @classmethod
    def _update_name_db(cls, first_name, last_name):
        from endoreg_db.models import FirstName, LastName

        FirstName.objects.get_or_create(name=first_name)
        LastName.objects.get_or_create(name=last_name)
