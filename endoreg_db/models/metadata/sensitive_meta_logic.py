from __future__ import annotations

# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
import logging
import os
import random
import re  # Neu hinzugefügt für Regex-Pattern
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Mapping, Optional, Protocol, Type, cast

from django.db import transaction
from django.utils import timezone

from endoreg_db.utils import guess_name_gender

# Assuming these utils are correctly located
from endoreg_db.utils.hashs import (
    get_patient_examination_hash,
    get_patient_hash,
)

# Import models needed for logic, use local imports inside functions if needed to break cycles
from ..administration import Center, Examiner, FirstName, LastName, Patient
from ..medical import PatientExamination
from ..other import Gender

if TYPE_CHECKING:
    from .sensitive_meta import SensitiveMeta  # Import model for type hinting


class _GenderManager(Protocol):
    def resolve_by_name(self, name: str) -> Gender | None: ...

    def get_or_create_by_name(
        self, *, name: str, defaults: dict[str, object]
    ) -> tuple[Gender, bool]: ...


class _SensitiveMetaIdentityLike(Protocol):
    pseudo_patient_id: int | None
    pseudo_examination_id: int | None


logger = logging.getLogger(__name__)
SECRET_SALT = os.getenv("DJANGO_SALT", "default_salt")
DEFAULT_UNKNOWN = "unknown"


# Regex-Pattern für verschiedene Datumsformate
ISO_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DE_RX = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def parse_any_date(s: str) -> Optional[date]:
    """
    Parst Datumsstring mit Priorität auf deutsches Format (DD.MM.YYYY).

    Unterstützte Formate:
    1. DD.MM.YYYY (Priorität) - deutsches Format
    2. YYYY-MM-DD (Fallback) - ISO-Format
    3. Erweiterte Fallbacks über dateparser

    Args:
        s: Datumsstring zum Parsen

    Returns:
        date-Objekt oder None bei ungültigem/fehlendem Input
    """
    if not s:
        return None

    s = s.strip()

    # 1. German dd.mm.yyyy (PRIORITÄT)
    if DE_RX.match(s):
        try:
            dd, mm, yyyy = s.split(".")
            return date(int(yyyy), int(mm), int(dd))
        except ValueError as e:
            logger.warning(f"Invalid German date format '{s}': {e}")
            return None

    # 2. ISO yyyy-mm-dd (Fallback für Rückwärtskompatibilität)
    if ISO_RX.match(s):
        try:
            return date.fromisoformat(s)
        except ValueError as e:
            logger.warning(f"Invalid ISO date format '{s}': {e}")
            return None

    # 3. Extended fallbacks
    try:
        # Try standard datetime parsing
        return datetime.fromisoformat(s).date()
    except ValueError:
        pass

    # Try dateparser with German locale preference
    import dateparser

    dt = dateparser.parse(
        s, settings={"DATE_ORDER": "DMY", "PREFER_DAY_OF_MONTH": "first"}
    )
    return dt.date() if dt else None


def format_date_german(d: Optional[date]) -> str:
    """
    Formatiert date-Objekt als deutsches Datumsformat (DD.MM.YYYY).

    Args:
        d: date-Objekt oder None

    Returns:
        Formatiertes Datum als String oder leerer String bei None
    """
    if not d:
        return ""
    return d.strftime("%d.%m.%Y")


def format_date_iso(d: Optional[date]) -> str:
    """
    Formatiert date-Objekt als ISO-Format (YYYY-MM-DD).

    Args:
        d: date-Objekt oder None

    Returns:
        Formatiertes Datum als String oder leerer String bei None
    """
    if not d:
        return ""
    return d.isoformat()


def generate_random_dob() -> datetime:
    """Generates a random timezone-aware datetime between 1920-01-01 and 2000-12-31."""
    start_date = date(1920, 1, 1)
    end_date = date(2000, 12, 31)
    time_between_dates = end_date - start_date
    days_between_dates = time_between_dates.days
    random_number_of_days = random.randrange(days_between_dates)
    random_date = start_date + timedelta(days=random_number_of_days)
    random_datetime = datetime.combine(random_date, datetime.min.time())
    return timezone.make_aware(random_datetime)


def generate_random_examination_date() -> date:
    """Generates a random date within the last 20 years."""
    today = date.today()
    start_date = today - timedelta(days=20 * 365)  # Approximate 20 years back
    time_between_dates = today - start_date
    days_between_dates = time_between_dates.days
    random_number_of_days = random.randrange(days_between_dates)
    random_date = start_date + timedelta(days=random_number_of_days)
    return random_date


def update_name_db(first_name: Optional[str], last_name: Optional[str]):
    """Adds first and last names to the respective lookup tables if they don't exist."""
    if first_name:
        FirstName.objects.get_or_create(name=first_name)
    if last_name:
        LastName.objects.get_or_create(name=last_name)


def calculate_patient_hash(instance: "SensitiveMeta", salt: str = SECRET_SALT) -> str:
    """Calculates the patient hash for the instance."""
    dob = instance.patient_dob
    first_name = instance.patient_first_name
    last_name = instance.patient_last_name
    center = instance.center

    if not dob:
        raise ValueError("Patient DOB is required to calculate patient hash.")
    if not center:
        raise ValueError("Center is required to calculate patient hash.")

    assert first_name is not None, "First name is required to calculate patient hash."
    assert last_name is not None, "Last name is required to calculate patient hash."

    hash_str = get_patient_hash(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center=center.name,  # Use center name
        salt=salt,
    )
    return sha256(hash_str.encode()).hexdigest()


def calculate_examination_hash(
    instance: "SensitiveMeta", salt: str = SECRET_SALT
) -> str:
    """Calculates the examination hash for the instance."""
    dob = instance.patient_dob
    first_name = instance.patient_first_name
    last_name = instance.patient_last_name
    examination_date = instance.examination_date
    center = instance.center

    if not dob:
        raise ValueError("Patient DOB is required to calculate examination hash.")
    if not examination_date:
        raise ValueError("Examination date is required to calculate examination hash.")
    if not center:
        raise ValueError("Center is required to calculate examination hash.")

    if not first_name:
        raise ValueError("First name is required to calculate examination hash.")
    if not last_name:
        raise ValueError("Last name is required to calculate examination hash.")

    hash_str = get_patient_examination_hash(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        examination_date=examination_date,
        center=center.name,  # Use center name
        salt=salt,
    )
    return sha256(hash_str.encode()).hexdigest()


def create_pseudo_examiner_logic(instance: "SensitiveMeta") -> "Examiner":
    """Creates or retrieves the pseudo examiner based on instance data."""
    first_name = instance.examiner_first_name
    last_name = instance.examiner_last_name
    center = instance.center  # Should be set before calling save

    if not first_name or not last_name or not center:
        logger.warning(
            f"Incomplete examiner info for SensitiveMeta (pk={instance.pk or 'new'}). Using default examiner."
        )
        # Ensure default center exists or handle appropriately
        default_center, _ = Center.objects.get_or_create(name="endoreg_db_demo")

        examiner, _created = Examiner.custom_get_or_create(
            first_name="Unknown", last_name="Unknown", center=default_center
        )
    else:
        examiner, _created = Examiner.custom_get_or_create(
            first_name=first_name, last_name=last_name, center=center
        )

    return examiner


def get_or_create_pseudo_patient_logic(instance: "SensitiveMeta"):
    """Gets or creates the pseudo patient based on instance data."""
    # Ensure necessary fields are set
    if not instance.patient_hash:
        instance.patient_hash = calculate_patient_hash(instance)
    if not instance.center:
        raise ValueError("Center must be set before creating pseudo patient.")
    if not instance.patient_gender:
        raise ValueError("Patient gender must be set before creating pseudo patient.")
    if not instance.patient_dob:
        raise ValueError("Patient DOB must be set before creating pseudo patient.")

    dob = instance.patient_dob
    year = dob.year
    month = dob.month

    patient, _created = Patient.get_or_create_pseudo_patient_by_hash(
        patient_hash=instance.patient_hash,
        center=instance.center,
        gender=instance.patient_gender,
        birth_year=year,
        birth_month=month,
    )
    return patient, _created


def get_or_create_pseudo_patient_examination_logic(
    instance: "SensitiveMeta",
):
    """Gets or creates the pseudo patient examination based on instance data."""
    # Ensure necessary fields are set
    if not instance.patient_hash:
        instance.patient_hash = calculate_patient_hash(instance)
    if not instance.examination_hash:
        instance.examination_hash = calculate_examination_hash(instance)

    # Ensure the pseudo patient exists first, as PatientExamination might depend on it
    if not instance.pseudo_patient:
        pseudo_patient, _created = get_or_create_pseudo_patient_logic(instance)
        instance.pseudo_patient = pseudo_patient  # Assign FK directly

    patient_examination, _created = (
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            patient_hash=instance.patient_hash,
            examination_hash=instance.examination_hash,
            # Optionally pass pseudo_patient if the method requires it
            # pseudo_patient=instance.pseudo_patient
        )
    )
    return patient_examination, _created


@transaction.atomic  # Ensure all operations within save succeed or fail together
def perform_save_logic(instance: "SensitiveMeta") -> "Examiner":
    """
    Contains the core logic for preparing a SensitiveMeta instance for saving.
    Handles data generation (dates), hash calculation, and linking pseudo-entities.

    This function is called on every save() operation and implements a two-phase approach:

    **Phase 1: Initial Creation (with defaults)**
    - When a SensitiveMeta is first created (e.g., via create_from_dict),
      it may have missing patient data (names, DOB, etc.)
    - Default values are set to prevent hash calculation errors:
      * patient_first_name: "unknown"
      * patient_last_name: "unknown"
      * patient_dob: random date (1920-2000)
    - A temporary hash is calculated using these defaults
    - Temporary pseudo-entities (Patient, Examination) are created

    **Phase 2: Update (with extracted data)**
    - When real patient data is extracted (e.g., from video OCR via lx_anonymizer),
      update_from_dict() is called with actual values
    - The instance fields are updated with real data (names, DOB, etc.)
    - save() is called again, triggering this function
    - Default-setting logic is skipped (fields are no longer empty)
    - Hash is RECALCULATED with real data
    - New pseudo-entities are created/retrieved based on new hash

    **Example Flow:**
    ```
    # Initial creation
    sm = SensitiveMeta.create_from_dict({"center": center})
    # → patient_first_name = "unknown", patient_last_name = "unknown"
    # → hash = sha256("unknown unknown 1990-01-01 ...")
    # → pseudo_patient_temp created

    # Later update with extracted data
    sm.update_from_dict({"patient_first_name": "Max", "patient_last_name": "Mustermann"})
    # → patient_first_name = "Max", patient_last_name = "Mustermann" (overwrites)
    # → save() triggered → perform_save_logic() called again
    # → Default-setting skipped (names already exist)
    # → hash = sha256("Max Mustermann 1985-03-15 ...") (RECALCULATED)
    # → pseudo_patient_real created/retrieved with new hash
    ```

    Args:
        instance: The SensitiveMeta instance being saved

    Returns:
        Examiner: The pseudo examiner instance to be linked via M2M after save

    Raises:
        ValueError: If required fields (center, gender) cannot be determined
    """

    # --- Pre-Save Checks and Data Generation ---

    # 1. Ensure DOB and Examination Date exist
    if not instance.patient_dob:
        logger.debug(
            f"SensitiveMeta (pk={instance.pk or 'new'}): Patient DOB missing, generating random."
        )
        instance.patient_dob = generate_random_dob()
    if not instance.examination_date:
        logger.debug(
            f"SensitiveMeta (pk={instance.pk or 'new'}): Examination date missing, generating random."
        )
        instance.examination_date = generate_random_examination_date()

    # 2. Ensure Center exists (should be set before calling save)
    if not instance.center:
        raise ValueError("Center must be set before saving SensitiveMeta.")

    # 2.5 CRITICAL: Set default patient names BEFORE hash calculation
    #
    # **Why this is necessary:**
    # Hash calculation (step 4) requires first_name and last_name to be non-None.
    # However, on initial creation (e.g., via get_or_create_sensitive_meta()), these
    # fields may be empty because real patient data hasn't been extracted yet.
    #
    # **Two-phase approach:**
    # - Phase 1 (Initial): Set defaults if names are missing
    #   → Allows hash calculation to succeed without errors
    #   → Creates temporary pseudo-entities with default hash
    #
    # - Phase 2 (Update): Real data extraction (OCR, manual input)
    #   → update_from_dict() sets real names ("Max", "Mustermann")
    #   → save() is called again
    #   → This block is SKIPPED (names already exist)
    #   → Hash is recalculated with real data (step 4)
    #   → New pseudo-entities created with correct hash
    #
    # **Example:**
    # Initial:  patient_first_name = "unknown" → hash = sha256("unknown unknown...")
    # Updated:  patient_first_name = "Max"     → hash = sha256("Max Mustermann...")
    #
    if not instance.patient_first_name:
        instance.patient_first_name = DEFAULT_UNKNOWN
        logger.debug(
            "SensitiveMeta (pk=%s): Patient first name missing, set to default '%s'.",
            instance.pk or "new",
            DEFAULT_UNKNOWN,
        )

    if not instance.patient_last_name:
        instance.patient_last_name = DEFAULT_UNKNOWN
        logger.debug(
            "SensitiveMeta (pk=%s): Patient last name missing, set to default '%s'.",
            instance.pk or "new",
            DEFAULT_UNKNOWN,
        )

    # 3. Ensure Gender exists (should be set before calling save, e.g., during creation/update)
    if not instance.patient_gender:
        # Use the now-guaranteed first_name for gender guessing
        first_name = instance.patient_first_name
        gender_str = guess_name_gender(first_name)
        if not gender_str:
            raise ValueError(
                "Patient gender could not be determined and must be set before saving."
            )
        # Convert string to Gender object
        gender_obj = cast(_GenderManager, Gender.objects).resolve_by_name(gender_str)
        if gender_obj is not None:
            instance.patient_gender = gender_obj
        else:
            # If the gender is 'unknown' (likely because name was DEFAULT_UNKNOWN),
            # we should auto-create it rather than crashing.
            if (
                gender_str == "unknown"
                or instance.patient_first_name == DEFAULT_UNKNOWN
            ):
                logger.warning(
                    f"Gender '{gender_str}' not found in DB. Auto-creating default entry."
                )
                gender_obj, _ = cast(
                    _GenderManager, Gender.objects
                ).get_or_create_by_name(
                    name="unknown",
                    defaults={
                        "abbreviation": "?",
                        "description": "Auto-created default gender",
                    },
                )
                instance.patient_gender = gender_obj
            else:
                # If it's a specific gender (e.g., 'male') that is missing,
                # that is a configuration error we should raise.
                raise ValueError(f"Gender '{gender_str}' not found in database.")
    # 4. Calculate Hashes (depends on DOB, Exam Date, Center, Names)
    #
    # **IMPORTANT: Hashes are RECALCULATED on every save!**
    # This enables the two-phase update pattern:
    # - Initial save: Hash based on default "unknown unknown" names
    # - Updated save: Hash based on real extracted names ("Max Mustermann")
    #
    # The new hash will link to different pseudo-entities, ensuring proper
    # anonymization while maintaining referential integrity.
    instance.patient_hash = calculate_patient_hash(instance)
    instance.examination_hash = calculate_examination_hash(instance)

    # 5. Get or Create Pseudo Patient (depends on hash, center, gender, dob)
    # Assign directly to the FK field to avoid premature saving issues
    pseudo_patient, _created = get_or_create_pseudo_patient_logic(instance)
    instance.pseudo_patient = pseudo_patient

    # 6. Get or Create Pseudo Examination (depends on hashes)
    # Assign directly to the FK field
    pseudo_examination, _created = get_or_create_pseudo_patient_examination_logic(
        instance
    )
    instance.pseudo_examination = pseudo_examination

    # 7. Get or Create Pseudo Examiner (depends on names, center)
    # This needs to happen *after* the main instance has a PK for M2M linking.
    # We create/get it here and return it to the main save method.
    examiner_instance = create_pseudo_examiner_logic(instance)

    # 8. Ensure SensitiveMetaState exists (will be checked/created *after* main save)

    # Return the examiner instance so the model's save method can handle M2M linking
    return examiner_instance


_PATIENT_DOB_FIELD_REFERENCES = frozenset(
    {
        "patient_dob",
        "patient_first_name",
        "patient_last_name",
        "examination_date",
    }
)
_EXAMINATION_DATE_FIELD_REFERENCES = frozenset(
    {
        "examination_date",
        "patient_first_name",
        "patient_last_name",
        "patient_dob",
    }
)


def _selected_model_data(
    cls: Type["SensitiveMeta"],
    data: Mapping[str, object],
    *,
    excluded_fields: frozenset[str] = frozenset(),
) -> dict[str, object]:
    field_names = _model_data_field_names(cls)
    return _filter_selected_data(data, field_names, excluded_fields)


def _model_data_field_names(cls: Type["SensitiveMeta"]) -> set[str]:
    return {field.name for field in cls._meta.get_fields() if _is_data_field(field)}


def _filter_selected_data(
    data: Mapping[str, object],
    field_names: set[str],
    excluded_fields: frozenset[str],
) -> dict[str, object]:
    return {
        key: value
        for key, value in data.items()
        if key in field_names and key not in excluded_fields
    }


def _is_data_field(field: object) -> bool:
    relation = getattr(field, "is_relation")
    return (
        not relation
        or getattr(field, "one_to_one")
        or (getattr(field, "many_to_one") and getattr(field, "related_model"))
    )


def _remove_invalid_date_field_reference(
    selected_data: dict[str, object],
    *,
    key: str,
    value: str,
    invalid_references: frozenset[str],
) -> bool:
    if value not in invalid_references:
        return False
    logger.warning(
        "Skipping invalid %s value '%s' - appears to be field name", key, value
    )
    selected_data.pop(key, None)
    return True


def _parse_create_patient_dob(value: str) -> datetime | None:
    import dateparser

    parsed_dob = dateparser.parse(
        value, languages=["de"], settings={"DATE_ORDER": "DMY"}
    )
    if parsed_dob is None:
        return None
    return timezone.make_aware(
        parsed_dob.replace(hour=0, minute=0, second=0, microsecond=0)
    )


def _normalize_create_patient_dob(selected_data: dict[str, object]) -> None:
    dob = selected_data.get("patient_dob")
    aware_dob = _aware_date_only(dob)
    if aware_dob is not None:
        selected_data["patient_dob"] = aware_dob
        logger.debug("Converted patient_dob from date to aware datetime: %s", aware_dob)
        return
    if isinstance(dob, str):
        _normalize_create_patient_dob_string(selected_data, dob)


def _aware_date_only(value: object) -> datetime | None:
    if not isinstance(value, date) or isinstance(value, datetime):
        return None
    return timezone.make_aware(datetime.combine(value, datetime.min.time()))


def _normalize_create_patient_dob_string(
    selected_data: dict[str, object], dob: str
) -> None:
    if _remove_invalid_date_field_reference(
        selected_data,
        key="patient_dob",
        value=dob,
        invalid_references=_PATIENT_DOB_FIELD_REFERENCES,
    ):
        return

    aware_dob = _parse_create_patient_dob(dob)
    _set_parsed_create_patient_dob(selected_data, dob, aware_dob)


def _set_parsed_create_patient_dob(
    selected_data: dict[str, object], original_value: str, parsed_value: datetime | None
) -> None:
    if parsed_value is None:
        logger.warning(
            "Could not parse patient_dob string '%s', removing from data",
            original_value,
        )
        selected_data.pop("patient_dob", None)
        return
    selected_data["patient_dob"] = parsed_value
    logger.debug(
        "Parsed string patient_dob '%s' to aware datetime: %s",
        original_value,
        parsed_value,
    )


def _parse_create_examination_date(value: str) -> date | None:
    if len(value) == 10 and value.count("-") == 2:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass

    import dateparser

    parsed_date = dateparser.parse(
        value, languages=["de"], settings={"DATE_ORDER": "DMY"}
    )
    return parsed_date.date() if parsed_date else None


def _normalize_create_examination_date(selected_data: dict[str, object]) -> None:
    exam_date = selected_data.get("examination_date")
    if not isinstance(exam_date, str):
        return
    if _remove_invalid_date_field_reference(
        selected_data,
        key="examination_date",
        value=exam_date,
        invalid_references=_EXAMINATION_DATE_FIELD_REFERENCES,
    ):
        return

    parsed_date = _parse_create_examination_date(exam_date)
    if parsed_date is None:
        logger.warning(
            "Could not parse examination_date string '%s', removing from data",
            exam_date,
        )
        selected_data.pop("examination_date", None)
        return
    selected_data["examination_date"] = parsed_date
    logger.debug(
        "Parsed string examination_date '%s' to date: %s",
        exam_date,
        parsed_date,
    )


def _resolve_create_center(data: Mapping[str, object]) -> Center:
    center = data.get("center")
    if center is not None:
        if not isinstance(center, Center):
            raise ValueError(f"'center' must be a Center instance, got {type(center)}")
        return center

    center_name = data.get("center_name")
    if not center_name:
        raise ValueError(
            "Either 'center' (Center object) or 'center_name' (string) is required in data dictionary."
        )
    try:
        return Center.objects.get(name=center_name)
    except Center.DoesNotExist:
        raise ValueError(f"Center with name '{center_name}' does not exist.")


def _get_or_create_gender(name: str) -> Gender:
    gender_obj = cast(_GenderManager, Gender.objects).resolve_by_name(name)
    if gender_obj is not None:
        return gender_obj
    gender_obj, _ = cast(_GenderManager, Gender.objects).get_or_create_by_name(
        name=name,
        defaults={
            "abbreviation": name[:1].upper() or None,
            "description": "Auto-created default gender entry",
        },
    )
    return gender_obj


def _resolve_create_gender(value: object, first_name: str) -> Gender:
    supplied_gender = _resolve_supplied_create_gender(value)
    if supplied_gender is not None:
        return supplied_gender

    gender_name = guess_name_gender(first_name)
    if not gender_name:
        logger.warning(
            "Could not guess gender for name '%s'. Setting Gender to unknown.",
            first_name,
        )
        gender_name = "unknown"
    return _get_or_create_gender(gender_name)


def _resolve_supplied_create_gender(value: object) -> Gender | None:
    if isinstance(value, Gender):
        return value
    if not isinstance(value, str):
        return None
    gender_obj = cast(_GenderManager, Gender.objects).resolve_by_name(value)
    if gender_obj is not None:
        return gender_obj
    logger.warning(
        "Gender with name '%s' provided but not found. Attempting to guess or use default.",
        value,
    )
    normalized = value.lower()
    return (
        _get_or_create_gender(normalized)
        if normalized in {"male", "female", "unknown"}
        else None
    )


def _set_create_examination_time(
    selected_data: dict[str, object], value: object
) -> None:
    try:
        from datetime import time as dt_time

        if isinstance(value, str):
            hour, minute = value.strip().split(":")[:2]
            selected_data["examination_time"] = dt_time(int(hour), int(minute))
        elif isinstance(value, datetime):
            selected_data["examination_time"] = value.time()
        elif isinstance(value, date):
            logger.debug(
                "examination_time value %s has no time component; skipping", value
            )
        else:
            selected_data["examination_time"] = value
    except ValueError as error:
        logger.warning("Invalid examination_time '%s': %s", value, error)


def _set_create_optional_fields(
    selected_data: dict[str, object], data: Mapping[str, object]
) -> None:
    _set_create_string_field(selected_data, data, key="file_path", strip=False)
    _set_create_string_field(selected_data, data, key="casenumber", strip=True)
    _set_create_time_from_data(selected_data, data)
    _set_create_anonymized_text(selected_data, data)


def _set_create_string_field(
    selected_data: dict[str, object],
    data: Mapping[str, object],
    *,
    key: str,
    strip: bool,
) -> None:
    value = data.get(key)
    if not value:
        return
    string_value = str(value)
    selected_data[key] = string_value.strip() if strip else string_value
    logger.debug("Set %s: %s", key, value)


def _set_create_time_from_data(
    selected_data: dict[str, object], data: Mapping[str, object]
) -> None:
    examination_time = data.get("examination_time")
    if examination_time:
        _set_create_examination_time(selected_data, examination_time)


def _set_create_anonymized_text(
    selected_data: dict[str, object], data: Mapping[str, object]
) -> None:
    anonymized_text = data.get("anonymized_text") or data.get("anonym_text")
    if not anonymized_text:
        return
    selected_data["anonymized_text"] = (
        anonymized_text.decode()
        if isinstance(anonymized_text, bytes)
        else str(anonymized_text)
    )
    logger.debug(
        "Set anonymized_text (length=%d)", len(selected_data["anonymized_text"])
    )


def create_sensitive_meta_from_dict(
    cls: Type["SensitiveMeta"], data: Mapping[str, object]
) -> "SensitiveMeta":
    """
    Create a SensitiveMeta instance from a dictionary.

    **Center handling:**
    This function accepts TWO ways to specify the center:
    1. `center` (Center object) - Directly pass a Center instance
    2. `center_name` (string) - Pass the center name as a string (will be resolved to Center object)

    At least ONE of these must be provided.

    **Example usage:**
    ```python
    # Option 1: With Center object
    data = {
        "patient_first_name": "Patient",
        "patient_last_name": "Unknown",
        "patient_dob": date(1990, 1, 1),
        "examination_date": date.today(),
        "center": center_obj,  # ← Center object
        "text": text #from extraction

    }
    sm = SensitiveMeta.create_from_dict(data)

    # Option 2: With center name string
    data = {
        "patient_first_name": "Patient",
        "patient_last_name": "Unknown",
        "patient_dob": date(1990, 1, 1),
        "examination_date": date.today(),
        "center_name": "university_hospital_wuerzburg",  # ← String
        "anonymized_text": "anonymized text"
    }
    sm = SensitiveMeta.create_from_dict(data)
    ```

    Args:
        cls: The SensitiveMeta class
        data: Dictionary containing field values

    Returns:
        SensitiveMeta: The created instance

    Raises:
        ValueError: If neither center nor center_name is provided
        ValueError: If center_name does not match any Center in database
    """

    selected_data = _selected_model_data(cls, data)
    _normalize_create_patient_dob(selected_data)
    _normalize_create_examination_date(selected_data)
    selected_data["center"] = _resolve_create_center(data)

    # Handle Names and Gender
    first_name = cast(str, selected_data.get("patient_first_name") or DEFAULT_UNKNOWN)
    last_name = cast(str, selected_data.get("patient_last_name") or DEFAULT_UNKNOWN)
    selected_data["patient_first_name"] = first_name  # Ensure defaults are set
    selected_data["patient_last_name"] = last_name

    selected_data["patient_gender"] = _resolve_create_gender(
        selected_data.get("patient_gender"), first_name
    )

    # Handle Text
    selected_data["text"] = data.get("text") or DEFAULT_UNKNOWN

    _set_create_optional_fields(selected_data, data)

    # Update name DB
    update_name_db(first_name, last_name)

    # Instantiate without saving yet
    sensitive_meta = cls(**selected_data)

    # Call save once at the end. This triggers the custom save logic.
    sensitive_meta.save()  # This will call perform_save_logic internally

    return sensitive_meta


def _update_center(
    instance: "SensitiveMeta",
    data: Mapping[str, object],
    selected_data: dict[str, object],
) -> None:
    center = data.get("center")
    if center is not None:
        if isinstance(center, Center):
            instance.center = center
            logger.debug("Updated center from Center object: %s", center.name)
        else:
            logger.warning(
                "Invalid center type %s, expected Center instance. Ignoring.",
                type(center),
            )
        selected_data.pop("center", None)
        return

    center_name = data.get("center_name")
    if not center_name:
        selected_data.pop("center", None)
        return
    try:
        instance.center = Center.objects.get(name=center_name)
        logger.debug("Updated center from center_name string: %s", center_name)
    except Center.DoesNotExist:
        logger.warning(
            "Center '%s' not found during update. Keeping existing center.", center_name
        )


def _unknown_gender_for_update(
    input_value: str, *, mapped: str | None
) -> Gender | None:
    unknown_gender = Gender.objects.filter(name__iexact="unknown").first()
    if unknown_gender is not None:
        suffix = "" if mapped else " (no mapping)"
        logger.warning(
            "Using 'unknown' gender as fallback for '%s'%s", input_value, suffix
        )
        return unknown_gender
    logger.error(
        "No 'unknown' gender found in database. Cannot handle gender '%s'. Skipping gender update.",
        input_value,
    )
    return None


def _resolve_update_gender_string(input_value: str) -> Gender | None:
    cleaned_value = input_value.strip()
    gender_obj = Gender.objects.filter(name__iexact=cleaned_value).first()
    if gender_obj is not None:
        logger.debug(
            "Successfully matched gender string '%s' to Gender object via iexact lookup",
            input_value,
        )
        return gender_obj

    mapped = _map_gender_string_to_standard(cleaned_value)
    if mapped:
        gender_obj = Gender.objects.filter(name__iexact=mapped).first()
        if gender_obj is not None:
            logger.info(
                "Mapped gender '%s' to '%s' via fallback mapping", input_value, mapped
            )
            return gender_obj
        logger.warning(
            "Mapped gender '%s' to '%s', but no such Gender in DB. Trying 'unknown'.",
            input_value,
            mapped,
        )
    return _unknown_gender_for_update(input_value, mapped=mapped)


def _update_selected_gender(
    selected_data: dict[str, object], input_value: object
) -> None:
    if isinstance(input_value, Gender):
        selected_data["patient_gender"] = input_value
        return
    if isinstance(input_value, str):
        gender_obj = _resolve_update_gender_string(input_value)
        if gender_obj is not None:
            selected_data["patient_gender"] = gender_obj
            return
    else:
        logger.warning(
            "Unexpected patient_gender type %s: %s. Skipping gender update.",
            type(input_value),
            input_value,
        )
    selected_data.pop("patient_gender", None)


def _set_update_examination_time(
    instance: "SensitiveMeta",
    selected_data: dict[str, object],
    value: object,
) -> None:
    try:
        from datetime import time as dt_time

        if isinstance(value, str) and ":" in value:
            hour, minute = value.strip().split(":")[:2]
            instance.examination_time = dt_time(int(hour), int(minute))
        elif isinstance(value, datetime):
            instance.examination_time = value.time()
    except ValueError as error:
        logger.warning("Skipping invalid examination_time '%s': %s", value, error)
        selected_data.pop("examination_time", None)


def _set_update_optional_field(
    instance: "SensitiveMeta",
    selected_data: dict[str, object],
    key: str,
    value: object,
) -> None:
    if key in {"file_path", "casenumber"}:
        setattr(instance, key, str(value))
    elif key in {"anonymized_text", "anonym_text"}:
        instance.anonymized_text = value if isinstance(value, str) else str(value)
    else:
        _set_update_examination_time(instance, selected_data, value)


def _set_update_optional_fields(
    instance: "SensitiveMeta",
    data: Mapping[str, object],
    selected_data: dict[str, object],
) -> None:
    for key in (
        "file_path",
        "casenumber",
        "examination_time",
        "anonymized_text",
        "anonym_text",
    ):
        value = data.get(key)
        if key in data and value is not None:
            _set_update_optional_field(instance, selected_data, key, value)


def _convert_update_field_value(key: str, value: object) -> tuple[bool, object]:
    if key == "patient_dob":
        return _convert_update_patient_dob(value)
    if key == "examination_date":
        return _convert_update_examination_date(value)
    return True, value


def _convert_update_patient_dob(value: object) -> tuple[bool, object]:
    parsed = parse_any_date(value) if isinstance(value, str) else value
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        aware_dob = timezone.make_aware(datetime.combine(parsed, datetime.min.time()))
        logger.debug(
            "Converted patient_dob to aware datetime during update: %s", aware_dob
        )
        return True, aware_dob
    if isinstance(value, str):
        logger.warning(
            "Could not parse patient_dob string '%s' during update, skipping", value
        )
        return False, value
    return True, value


def _convert_update_examination_date(value: object) -> tuple[bool, object]:
    if not isinstance(value, str):
        return True, value
    parsed_date = parse_any_date(value)
    if parsed_date is None:
        logger.warning(
            "Could not parse examination_date string '%s' during update, skipping",
            value,
        )
        return False, value
    logger.debug(
        "Parsed string examination_date '%s' during update to date: %s",
        value,
        parsed_date,
    )
    return True, parsed_date


def _apply_selected_updates(
    instance: "SensitiveMeta",
    selected_data: Mapping[str, object],
    *,
    explicit_examiner_fields: frozenset[str],
) -> bool:
    patient_name_changed = False
    for key, value in selected_data.items():
        changed = _apply_selected_update(
            instance,
            key,
            value,
            explicit_examiner_fields=explicit_examiner_fields,
        )
        patient_name_changed = patient_name_changed or changed
    return patient_name_changed


def _apply_selected_update(
    instance: "SensitiveMeta",
    key: str,
    value: object,
    *,
    explicit_examiner_fields: frozenset[str],
) -> bool:
    if value is None:
        logger.debug("Skipping field '%s' during update because value is None", key)
        return False
    if key in explicit_examiner_fields:
        return False
    should_set, converted_value = _convert_update_field_value(key, value)
    if not should_set:
        return False
    name_changed = _patient_name_changed(instance, key, converted_value)
    setattr(instance, key, converted_value)
    return name_changed


def _patient_name_changed(instance: "SensitiveMeta", key: str, value: object) -> bool:
    return (
        key in {"patient_first_name", "patient_last_name"}
        and getattr(instance, key) != value
    )


def _update_examiner_names(
    instance: "SensitiveMeta", data: Mapping[str, object]
) -> frozenset[str]:
    explicit_fields: set[str] = set()
    examiner_first_name = cast(str | None, data.get("examiner_first_name"))
    examiner_last_name = cast(str | None, data.get("examiner_last_name"))
    if examiner_first_name is not None:
        instance.examiner_first_name = examiner_first_name
        explicit_fields.add("examiner_first_name")
    if examiner_last_name is not None:
        instance.examiner_last_name = examiner_last_name
        explicit_fields.add("examiner_last_name")
    return frozenset(explicit_fields)


def _update_name_db_if_changed(
    instance: "SensitiveMeta", patient_name_changed: bool
) -> None:
    if not patient_name_changed:
        return
    update_name_db(instance.patient_first_name, instance.patient_last_name)


def _save_updated_sensitive_meta(instance: "SensitiveMeta") -> None:
    instance.save()


def update_sensitive_meta_from_dict(
    instance: "SensitiveMeta", data: Mapping[str, object]
) -> "SensitiveMeta":
    """
    Updates a SensitiveMeta instance from a dictionary of new values.

    **Integration with two-phase save pattern:**
    This function is typically called after initial SensitiveMeta creation when real
    patient data becomes available (e.g., extracted from video OCR, report parsing, or
    manual annotation).

    **Example workflow:**
    ```python
    # Phase 1: Initial creation with defaults
    sm = SensitiveMeta.create_from_dict({"center": center})
    # → patient_first_name = "unknown", hash = sha256("unknown...")

    # Phase 2: Update with extracted data
    extracted = {
        "patient_first_name": "Max",
        "patient_last_name": "Mustermann",
        "patient_dob": date(1985, 3, 15)
    }
    update_sensitive_meta_from_dict(sm, extracted)
    # → Sets: sm.patient_first_name = "Max", sm.patient_last_name = "Mustermann"
    # → Calls: sm.save()
    # → Triggers: perform_save_logic() again
    # → Result: Hash recalculated with real data, new pseudo-entities created
    ```

    **Key behaviors:**
    - Updates instance attributes from provided dictionary
    - Handles type conversions (date strings → date objects, gender strings → Gender objects)
    - Tracks patient name changes to update name database
    - Calls save() at the end, triggering full save logic including hash recalculation
    - Default-setting in perform_save_logic() is skipped (fields already populated)

    Args:
        instance: The existing SensitiveMeta instance to update
        data: Dictionary of field names and new values

    Returns:
        The updated SensitiveMeta instance

    Raises:
        Exception: If save fails or required conversions fail
    """
    selected_data = _selected_model_data(
        type(instance),
        data,
        excluded_fields=frozenset({"pseudo_patient", "pseudo_examination"}),
    )
    _update_center(instance, data, selected_data)

    # Set examiner names if provided, before calling save
    explicit_examiner_fields = _update_examiner_names(instance, data)

    # Handle patient_gender specially with graceful error handling
    patient_gender_input = data.get("patient_gender")
    if patient_gender_input is not None:
        _update_selected_gender(selected_data, patient_gender_input)

    _set_update_optional_fields(instance, data, selected_data)

    # Update other attributes from selected_data
    patient_name_changed = _apply_selected_updates(
        instance,
        selected_data,
        explicit_examiner_fields=explicit_examiner_fields,
    )

    # Update name DB if patient names changed
    _update_name_db_if_changed(instance, patient_name_changed)

    # Call save - this will trigger the full save logic including hash recalculation etc.
    _save_updated_sensitive_meta(instance)

    return instance


def update_or_create_sensitive_meta_from_dict(
    cls: Type["SensitiveMeta"],
    data: Mapping[str, object],
    instance: Optional["SensitiveMeta"] = None,
):
    """Logic to update or create a SensitiveMeta instance from a dictionary."""
    # Check if the instance already exists based on unique fields
    sensitive_meta: "SensitiveMeta"
    _created: bool
    if instance:
        # Update the existing instance
        sensitive_meta = update_sensitive_meta_from_dict(instance, data)
        _created = False

    else:
        # Create a new instance
        sensitive_meta = create_sensitive_meta_from_dict(cls, data)
        _created = True
    return sensitive_meta, _created


def _map_gender_string_to_standard(gender_str: str) -> Optional[str]:
    """Maps various gender string inputs to standard gender names used in the DB."""
    mapping = {
        "male": ["male", "m", "männlich", "man"],
        "female": ["female", "f", "weiblich", "woman"],
        "unknown": ["unknown", "unbekannt", "other", "diverse", ""],
    }
    gender_lower = gender_str.strip().lower()
    for standard, variants in mapping.items():
        if gender_lower in variants:
            return standard
    return None


def _create_anonymized_record(
    instance: "SensitiveMeta",
    DEFAULT_ANONYMIZED: str = "None",
    DEFAULT_ANONYMIZED_DATE: datetime = timezone.make_aware(datetime(1900, 1, 1)),
    *,
    preserve_identity: bool = True,
) -> None:
    """
    Create a SensitiveMeta instance with all sensitive fields set to anonymized defaults.
    This is only called after anonymization and will delete all data that can identify a patient from the database.
    What is left will only be the patient hash.

    Args:
        instance: The existing SensitiveMeta instance to anonymize
        DEFAULT_ANONYMIZED: Usually None, The default string to use for anonymized fields (e.g., "anonymized,")
    """

    instance.refresh_from_db()
    committed_identity: dict[str, str | int | None] = {
        "patient_hash": instance.patient_hash,
        "examination_hash": instance.examination_hash,
        "pseudo_patient_id": cast(
            _SensitiveMetaIdentityLike, instance
        ).pseudo_patient_id,
        "pseudo_examination_id": cast(
            _SensitiveMetaIdentityLike, instance
        ).pseudo_examination_id,
    }
    patient_hash = instance.get_patient_hash()
    instance.get_patient_examination_hash()

    pseudo_patient = None
    dob_value = instance.patient_dob
    dob_date: date | None
    if isinstance(dob_value, datetime):
        dob_date = dob_value.date()
    else:
        dob_date = dob_value

    if (
        patient_hash
        and instance.center is not None
        and instance.patient_gender is not None
        and dob_date is not None
    ):
        pseudo_patient, _created = Patient.get_or_create_pseudo_patient_by_hash(
            patient_hash=patient_hash,
            center=instance.center,
            gender=instance.patient_gender,
            birth_month=dob_date.month,
            birth_year=dob_date.year,
        )
    elif patient_hash:
        pseudo_patient, _created = Patient.get_or_create_pseudo_patient_by_hash(
            patient_hash=patient_hash
        )

    if pseudo_patient and pseudo_patient.dob:
        preserved_dob = timezone.make_aware(
            datetime.combine(pseudo_patient.dob, datetime.min.time())
        )
    else:
        preserved_dob = DEFAULT_ANONYMIZED_DATE

    anonymized_data = {
        "patient_first_name": DEFAULT_ANONYMIZED,
        "patient_last_name": DEFAULT_ANONYMIZED,
        "patient_dob": preserved_dob,
        "examination_date": DEFAULT_ANONYMIZED_DATE,
        "patient_gender": pseudo_patient.gender
        if pseudo_patient
        else instance.patient_gender,
        "center": pseudo_patient.center if pseudo_patient else instance.center,
    }
    sensitive_meta = update_sensitive_meta_from_dict(instance, anonymized_data)

    if preserve_identity:
        # The anonymized fields must not become the new case identity. Restore the
        # validated hash/FK identity directly so SensitiveMeta.save() cannot
        # recalculate it from anonymized placeholders.
        update_fields: dict[str, str | int] = {
            key: value for key, value in committed_identity.items() if value is not None
        }
        if update_fields:
            sensitive_meta.__class__.objects.filter(pk=sensitive_meta.pk).update(
                **update_fields
            )
            for key, value in update_fields.items():
                setattr(sensitive_meta, key, value)
        return

    sensitive_meta.save()
