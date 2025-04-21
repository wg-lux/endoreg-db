# DevLog

# filepath: /home/admin/dev/endo-ai/endoreg-db/endoreg_db/models/metadata/sensitive_meta.py
from django.db import models
# Removed hash utils, datetime, random, os, timezone, sha256 imports
# Removed icecream import (was used in old save logic)
from typing import TYPE_CHECKING, Dict, Any, Type

# Import logic functions
from . import sensitive_meta_logic as logic
# Import models needed for type hints and FKs
from ..state import SensitiveMetaState # Needed for post-save state check

if TYPE_CHECKING:
    from ..administration import (
        Center,
        Examiner,
        Patient,
        FirstName, # Keep for type hinting if needed
        LastName   # Keep for type hinting if needed
    )
    from ..other import Gender
    from ..medical import PatientExamination
    # from ..state import SensitiveMetaState # Already imported above


# SECRET_SALT moved to logic

class SensitiveMeta(models.Model):
    """
    Stores potentially sensitive information extracted from media.
    Logic for creation, hashing, pseudo-anonymization, and saving is in sensitive_meta_logic.py.
    """
    examination_date = models.DateField(blank=True, null=True)
    pseudo_patient = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        help_text="FK to the pseudo-anonymized Patient record."
    )
    patient_first_name = models.CharField(max_length=255, blank=True, null=True)
    patient_last_name = models.CharField(max_length=255, blank=True, null=True)
    patient_dob = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Date of birth (can be auto-generated if missing)."
    )
    pseudo_examination = models.ForeignKey(
        "PatientExamination",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        help_text="FK to the pseudo-anonymized PatientExamination record."
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
        help_text="Pseudo-anonymized examiner(s) associated with the examination."
    )
    center = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        blank=True, # Should ideally be False if always required before save
        null=True,  # Should ideally be False
    )

    # Raw examiner names stored temporarily until pseudo-examiner is created/linked
    examiner_first_name = models.CharField(max_length=255, blank=True, null=True, editable=False)
    examiner_last_name = models.CharField(max_length=255, blank=True, null=True, editable=False)

    # Hashes calculated and stored by save logic
    examination_hash = models.CharField(max_length=64, blank=True, null=True, editable=False, db_index=True) # Use 64 for sha256 hex
    patient_hash = models.CharField(max_length=64, blank=True, null=True, editable=False, db_index=True) # Use 64 for sha256 hex

    endoscope_type = models.CharField(max_length=255, blank=True, null=True)
    endoscope_sn = models.CharField(max_length=255, blank=True, null=True)

    # Removed state_verified property, assuming state is handled via the related SensitiveMetaState model

    if TYPE_CHECKING:
        examiners: models.QuerySet["Examiner"]
        pseudo_patient: "Patient"
        patient_gender: "Gender"
        pseudo_examination: "PatientExamination"
        state: "SensitiveMetaState" # Assuming related_name='state' is defined on SensitiveMetaState.origin
        center: "Center"

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
    # Keep simple getters if needed, but creation logic is centralized.

    def get_pseudo_examiner(self) -> "Examiner" | None:
        """Returns the linked pseudo examiner, if one exists."""
        if self.pk:
            return self.examiners.first()
        return None # Cannot determine before saving and linking

    # Removed create_pseudo_examiner - logic is now in sensitive_meta_logic.create_pseudo_examiner_logic

    def get_pseudo_patient(self) -> "Patient" | None:
        """Returns the linked pseudo patient, if one exists."""
        return self.pseudo_patient # Access the FK directly

    # Removed create_pseudo_patient - logic is now in sensitive_meta_logic.get_or_create_pseudo_patient_logic

    def get_pseudo_patient_examination(self) -> "PatientExamination" | None:
        """Returns the linked pseudo patient examination, if one exists."""
        return self.pseudo_examination # Access the FK directly

    # Removed get_or_create_pseudo_patient_examination - logic is now in sensitive_meta_logic.get_or_create_pseudo_patient_examination_logic

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
        dob_str = str(self.patient_dob.date()) if self.patient_dob else "None" # Show only date part
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
            try:
                # Access state verification through the related state object
                state_verified = str(self.state.is_verified) if hasattr(self, 'state') and self.state else "No State"
            except SensitiveMetaState.DoesNotExist:
                 state_verified = "No State"
            except AttributeError:
                 # Handle case where 'state' related name isn't set up or instance not saved
                 state_obj = SensitiveMetaState.objects.filter(origin_id=self.pk).first()
                 state_verified = str(state_obj.is_verified) if state_obj else "No State"
        else:
            state_verified = "[Not saved yet]"


        return (
            f"SensitiveMeta(pk={self.pk}): "
            f"Patient={self.patient_last_name}, {self.patient_first_name} (*{dob_str}, {gender_str}), "
            f"ExamDate={exam_date_str}, Center={center_name}, "
            f"Examiners={examiners_str}, StateVerified={state_verified}, "
            f"PatientHash={self.patient_hash[-8:] if self.patient_hash else 'None'}, " # Show last 8 chars
            f"ExamHash={self.examination_hash[-8:] if self.examination_hash else 'None'}" # Show last 8 chars
        )


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
        Overrides the default save method to ensure data integrity, calculate hashes,
        link pseudo-entities, and manage related state using external logic.
        """
        # 1. Call the main logic function to perform pre-save checks, data generation,
        #    and creation/linking of pseudo patient/examination FKs.
        #    This function modifies the instance fields (hashes, FKs, dates).
        #    It returns the examiner instance to be linked *after* saving.
        examiner_to_link = logic.perform_save_logic(self) # Pass only self

        # 2. Call the original Django save method to save the instance itself
        #    (including updated FKs, hashes, dates).
        super().save(*args, **kwargs) # Pass original args/kwargs

        # 3. Ensure SensitiveMetaState exists *after* saving the main instance
        #    Use related name 'state' if defined, otherwise access via manager.
        if self.pk: # Ensure we have a PK
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

    @classmethod
    def _update_name_db(cls, first_name, last_name):
        # Delegate to logic
        logic.update_name_db(first_name, last_name)

```

### /home/admin/dev/endo-ai/endoreg-db/endoreg_db/models/metadata/video_meta.py

Remove the duplicate `SensitiveMeta` model definition from this file.

```python
# filepath: /home/admin/dev/endo-ai/endoreg-db/endoreg_db/models/metadata/video_meta.py
from django.db import models
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

# import endoreg_center_id from django settings
from django.conf import settings

# check if endoreg_center_id is set
if not hasattr(settings, "ENDOREG_CENTER_ID"):
    ENDOREG_CENTER_ID = 9999
else:
    ENDOREG_CENTER_ID = settings.ENDOREG_CENTER_ID

# Import the new utility function
from ...utils.ffmpeg_wrapper import get_stream_info

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..administration import Center
    from ..medical.hardware import EndoscopyProcessor, Endoscope
    # Removed reference to SensitiveMeta here


# VideoMeta
class VideoMeta(models.Model):
    # ... existing code ...

    def get_endo_roi(self):
        # ... existing code ...

    @property
    def fps(self) -> Optional[float]:
        # ... existing code ...

    @property
    def duration(self) -> Optional[float]:
        # ... existing code ...

    @property
    def width(self) -> Optional[int]:
        # ... existing code ...

    @property
    def height(self) -> Optional[int]:
        # ... existing code ...

    @property
    def frame_count(self) -> Optional[int]:
        # ... existing code ...


class FFMpegMeta(models.Model):
    # ... existing code ...

    @property
    def fps(self) -> Optional[float]:
        # ... existing code ...

    @classmethod
    def create_from_file(cls, file_path: Path):
        # ... existing code ...

    def __str__(self):
        # ... existing code ...


class VideoImportMeta(models.Model):
    # ... existing code ...

    def __str__(self):
        # ... existing code ...


# Removed duplicate SensitiveMeta model definition
# class SensitiveMeta(models.Model):
#     """
#     Stores potentially sensitive information extracted from media, often via OCR or other analysis.
#     Includes validation status.
#     """
#     is_validated = models.BooleanField(default=False, help_text="Indicates if the sensitive metadata has been validated.")
```

### /home/admin/dev/endo-ai/endoreg-db/endoreg_db/models/metadata/pdf_meta.py

Remove the duplicate `SensitiveMeta` model definition from this file.

```python
# filepath: /home/admin/dev/endo-ai/endoreg-db/endoreg_db/models/metadata/pdf_meta.py
from django.db import models
from django.core.files import File
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..media.pdf.report_reader.report_reader_flag import ReportReaderFlag
    # Removed reference to SensitiveMeta here

class PdfType(models.Model):
    # ... existing code ...

    @classmethod
    def default_pdf_type(cls):
        # ... existing code ...

class PdfMeta(models.Model):
    # ... existing code ...

    @classmethod
    def create_from_file(cls, pdf_file):
        # ... existing code ...

# Removed duplicate SensitiveMeta model definition
# class SensitiveMeta(models.Model):
#     """
#     Stores potentially sensitive information extracted from media, often via OCR or other analysis.
#     Includes validation status.
#     """
#     is_validated = models.BooleanField(default=False, help_text="Indicates if the sensitive metadata has been validated.")
```


# EndoReg DB Models
Summary by submodules.

## Administration
`endoreg_db.models.administration`

## Label
Includes `Label`, `LabelSet`, `LabelVideoSegment`, `ImageClassificationAnnotation`. `LabelVideoSegment` and `ImageClassificationAnnotation` now link directly to `VideoFile` and `Frame` respectively.

## Media
Includes `VideoFile` (the unified model for videos), `Frame`, `ImageFile`, `DocumentFile`.

## Medical
Includes `Patient`, `PatientExamination`, `PatientFinding`, `EndoscopyProcessor`, `Endoscope`, etc.

## Metadata
Includes `ModelMeta`, `VideoMeta`, `SensitiveMeta`, `VideoPredictionMeta`, etc. `VideoMeta` and `VideoPredictionMeta` now link directly to `VideoFile`.

## Other
Includes `InformationSource`.

## Requirement
*(No changes needed)*

## Rule
*(No changes needed)*

## State
Includes `VideoState`, `ReportState`, etc. `VideoState` now links directly to `VideoFile`.