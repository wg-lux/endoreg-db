import logging

# Removed hash utils, datetime, random, os, timezone, sha256 imports
# Removed icecream import (was used in old save logic)
from typing import TYPE_CHECKING, Any, Dict, Self, Type, cast

from django.db import models

from ..administration.person.patient import PatientExternalID

# Import models needed for type hints and FKs
from ..state import SensitiveMetaState  # Needed for post-save state check

# Import logic functions
from . import sensitive_meta_logic as logic

if TYPE_CHECKING:
    from ..administration import (
        Center,
        Examiner,
        Patient,  # Keep for type hinting if needed
    )
    from ..medical import PatientExamination
    from ..other import Gender
    # from ..state import SensitiveMetaState # Already imported above

logger = logging.getLogger(__name__)  # Add logger instance

# SECRET_SALT moved to logic


class SensitiveMeta(models.Model):
    """
    Stores potentially sensitive information extracted from media.
    Logic for creation, hashing, pseudo-anonymization, and saving is in sensitive_meta_logic.py.
    """

    # --- Examination and Patient Info ---
    examination_date = models.DateField(blank=True, null=True)
    examination_time = models.TimeField(blank=True, null=True)
    casenumber = models.CharField(max_length=255, blank=True, null=True)
    file_path = models.CharField(max_length=1024, blank=True, null=True)

    # --- Core FKs ---
    pseudo_patient = models.ForeignKey("Patient", on_delete=models.CASCADE, blank=True, null=True, help_text="FK to the pseudo-anonymized Patient record.")
    pseudo_examination = models.ForeignKey(
        "PatientExamination", on_delete=models.CASCADE, blank=True, null=True, help_text="FK to the pseudo-anonymized PatientExamination record."
    )
    patient_gender = models.ForeignKey("Gender", on_delete=models.CASCADE, blank=True, null=True)
    examiners = models.ManyToManyField("Examiner", blank=True, help_text="Pseudo-anonymized examiner(s)")
    center = models.ForeignKey("Center", on_delete=models.CASCADE, blank=True, null=True)

    # --- Names and DOB ---
    patient_first_name = models.CharField(max_length=255, blank=True, null=True)
    patient_last_name = models.CharField(max_length=255, blank=True, null=True)
    patient_dob = models.DateTimeField(blank=True, null=True, help_text="Date of birth (can be auto-generated).")

    examiner_first_name = models.CharField(max_length=255, blank=True, null=True, editable=False)
    examiner_last_name = models.CharField(max_length=255, blank=True, null=True, editable=False)

    # --- Hashes ---
    patient_hash = models.CharField(max_length=64, blank=True, null=True, editable=False, db_index=True)
    examination_hash = models.CharField(max_length=64, blank=True, null=True, editable=False, db_index=True)

    # --- Endoscope Info ---
    endoscope_type = models.CharField(max_length=255, blank=True, null=True)
    endoscope_sn = models.CharField(max_length=255, blank=True, null=True)

    # --- External patient ID ---
    external_id = models.ForeignKey("PatientExternalID", on_delete=models.CASCADE, blank=True, null=True)

    if TYPE_CHECKING:
        pseudo_patient: models.ForeignKey["Patient|None"]

        patient_gender: models.ForeignKey["Gender|None"]
        pseudo_examination: models.ForeignKey["PatientExamination|None"]
        state: models.ForeignKey["SensitiveMetaState|None"]  # Assuming related_name='state' is defined on SensitiveMetaState.origin
        center: models.ForeignKey["Center|None"]

        examiners = cast(models.manager.RelatedManager["Examiner"], examiners)

    @property
    def external_id_origin(self) -> str | None:
        """Returns the origin system from the linked external ID, if available."""
        if self.external_id:
            return self.external_id.origin
        return None

    # --- Text Fields ---
    text = models.TextField(blank=True, null=True)
    anonymized_text = models.TextField(blank=True, null=True)
    
    # --- Anonymization helper method ---
    create_anonymized_record = logic._create_anonymized_record

    # --- State ---
    @property
    def state_verified(self) -> bool | None:
        """
        Convenience alias to check if the related state is verified.
        Returns None if state is not set.
        """
        if hasattr(self, "state") and self.state is not None:
            return self.state.is_verified
        return None

    @staticmethod
    def _generate_random_dob():
        # Delegate to logic
        return logic.generate_random_dob()

    @staticmethod
    def _generate_random_examination_date():
        # Delegate to logic
        return logic.generate_random_examination_date()

    @classmethod
    def create_from_dict(cls: Type["SensitiveMeta"], data: Dict[str, Any]):
        """Creates a SensitiveMeta instance from a dictionary using external logic."""
        # Delegate to logic function
        return logic.create_sensitive_meta_from_dict(cls, data)

    # --- Methods related to pseudo-entities are now primarily handled within save logic ---

    def get_pseudo_examiner(self) -> "Examiner | None":
        """Returns the linked pseudo examiner, if one exists."""
        if self.pk:
            return self.examiners.first()
        return None  # Cannot determine before saving and linking

    # --- Update method delegates to logic ---
    def update_from_dict(self, data: Dict[str, Any]):
        """Updates the instance from a dictionary using external logic."""
        # Delegate to logic function
        return logic.update_sensitive_meta_from_dict(self, data)

    # --- String representation ---
    def __str__(self):
        # Keep this method for basic representation, ensure fields are accessed safely
        center_name = self.center.name if self.center else "None"
        gender_str = str(self.patient_gender) if self.patient_gender else "None"
        dob_str = str(self.patient_dob.date()) if self.patient_dob else "None"  # Show only date part
        exam_date_str = str(self.examination_date) if self.examination_date else "None"

        examiners_str = "[Not saved yet]"
        if self.pk:
            try:
                # Use prefetch_related in queries accessing this for efficiency
                examiners_str = ", ".join([str(e) for e in self.examiners.all()]) or "[None]"
            except Exception as e:
                examiners_str = f"[Error: {e}]"

        state_verified = "Unknown"
        if self.pk:
            # Access state verification through the related state object
            state_verified = str(self.is_verified)
        else:
            state_verified = "[Not saved yet]"

        return (
            f"SensitiveMeta(pk={self.pk}): "
            f"Patient={self.patient_last_name}, {self.patient_first_name} (*{dob_str}, {gender_str}), "
            f"ExamDate={exam_date_str}, Center={center_name}, "
            f"Examiners={examiners_str}, StateVerified={state_verified}, "
            f"PatientHash={str(self.patient_hash)[-8:] if self.patient_hash else 'None'}, "  # Show last 8 chars
            f"ExamHash={str(self.examination_hash)[-8:] if self.examination_hash else 'None'}"  # Show last 8 chars
        )

    @property
    def state_safe(self) -> "SensitiveMetaState":
        state = self.state
        if not state:
            raise SensitiveMetaState.DoesNotExist("SensitiveMetaState does not exist for this SensitiveMeta instance.")
        return state

    @property
    def is_verified(self):
        """
        Checks if the instance is verified based on the related state object.
        """
        # Use try-except for robustness, especially if state might not exist yet
        try:
            # Access the related state object directly via the 'state' attribute
            # This assumes the related_name on SensitiveMetaState.origin is 'state'
            return self.state_safe.is_verified
        except SensitiveMetaState.DoesNotExist:
            # If the state object doesn't exist, it's not verified
            return False
        except AttributeError:
            # If the 'state' attribute doesn't exist (e.g., before first save), it's not verified
            return False

    def get_or_create_state(self) -> "SensitiveMetaState":
        """
        Gets the related SensitiveMetaState instance, creating one if it doesn't exist.
        Does not save the SensitiveMeta instance itself.
        """
        try:
            state = self.state_safe
            return state

        except SensitiveMetaState.DoesNotExist:
            # If it doesn't exist, create it
            logger.info("Creating new SensitiveMetaState for SensitiveMeta %s", self.pk)
            # Create the state, linking it back to this instance
            # The 'origin' field on SensitiveMetaState points back to this SensitiveMeta instance
            new_state = SensitiveMetaState.objects.create(origin=self)
            # Assign the newly created state to the instance's 'state' attribute
            # This avoids needing to query again immediately
            self.state = new_state
            return new_state
        except AttributeError:
            # Fallback if related_name is not 'state' or instance not saved yet (no PK)
            if self.pk:
                state, created = SensitiveMetaState.objects.get_or_create(origin=self)
                if created:
                    logger.info("Created new SensitiveMetaState for SensitiveMeta %s (via get_or_create)", self.pk)
                # Link the state back to the instance in memory
                self.state = state
                return state
            else:
                # Cannot create state if the main instance has no PK
                raise ValueError("Cannot get or create state for an unsaved SensitiveMeta instance.")

    def __repr__(self):
        return self.__str__()

    # --- Hashing methods delegate to logic ---
    def get_patient_hash(self, salt=None):
        """Calculates the patient hash using external logic."""
        # Use default salt from logic if None is passed
        salt_to_use = salt if salt is not None else logic.SECRET_SALT
        # Delegate to logic function
        return logic.calculate_patient_hash(self, salt=salt_to_use)

    def get_patient_examination_hash(self, salt=None):
        """Calculates the examination hash using external logic."""
        salt_to_use = salt if salt is not None else logic.SECRET_SALT
        # Delegate to logic function
        return logic.calculate_examination_hash(self, salt=salt_to_use)

    # --- Save method orchestrates calls to logic ---
    def save(self, *args, **kwargs):
        """
        Saves the SensitiveMeta instance, ensuring data integrity, hash calculation, pseudo-entity linking, and related state management using external logic.

        This method performs pre-save operations via external logic, persists the instance, ensures the related SensitiveMetaState exists, and links the appropriate examiner to the instance.
        """
        # 1. Call the main logic function to perform pre-save checks, data generation,
        #    and creation/linking of pseudo patient/examination FKs.
        #    This function modifies the instance fields (hashes, FKs, dates).
        #    It returns the examiner instance to be linked *after* saving.
        examiner_to_link = logic.perform_save_logic(self)  # Pass only self

        # 2. Call the original Django save method to save the instance itself
        #    (including updated FKs, hashes, dates).
        super().save(*args, **kwargs)  # Pass original args/kwargs

        # 3. Ensure SensitiveMetaState exists *after* saving the main instance
        #    Use related name 'state' if defined, otherwise access via manager.
        if self.pk:  # Ensure we have a PK
            try:
                # Check if the related state object already exists using the related manager
                _ = self.state
            except SensitiveMetaState.DoesNotExist:
                # If not, create a new one, linking it to the saved instance
                SensitiveMetaState.objects.create(origin=self)
            except AttributeError:
                # Fallback check if 'state' related_name is missing
                if not SensitiveMetaState.objects.filter(origin=self).exists():
                    SensitiveMetaState.objects.create(origin=self)

        # 4. Handle ManyToMany linking (examiners) *after* the instance has a PK.
        if examiner_to_link and self.pk and not self.examiners.filter(pk=examiner_to_link.pk).exists():
            self.examiners.add(examiner_to_link)
            # Adding to M2M handles its own DB interaction, no second super().save() needed.

    def mark_dob_verified(self):
        """
        Mark the associated date of birth as verified in the related SensitiveMetaState.
        """
        state = self.get_or_create_state()
        state.mark_dob_verified()

    def mark_names_verified(self):
        """
        Mark the patient's names as verified in the associated verification state.

        This method ensures the related SensitiveMetaState exists and updates its status to indicate that the patient's names have been verified.
        """
        state = self.get_or_create_state()
        state.mark_names_verified()

    @classmethod
    def _update_name_db(cls, first_name, last_name):
        # Delegate to logic
        """
        Update the name database with the provided first and last names using external logic.

        This method delegates the update operation to the external logic module responsible for managing name data.
        """
        logic.update_name_db(first_name, last_name)
