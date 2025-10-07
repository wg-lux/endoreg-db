import logging
import os
import random
import re  # Neu hinzugefügt für Regex-Pattern
from hashlib import sha256
from datetime import datetime, timedelta, date
from typing import TYPE_CHECKING, Dict, Any, Optional, Type

from django.utils import timezone
from django.db import transaction

# Assuming these utils are correctly located
from endoreg_db.utils.hashs import get_patient_hash, get_patient_examination_hash
from endoreg_db.utils import guess_name_gender

# Import models needed for logic, use local imports inside functions if needed to break cycles
from ..administration import Center, Examiner, Patient, FirstName, LastName
from ..other import Gender
from ..medical import PatientExamination
from ..state import SensitiveMetaState


if TYPE_CHECKING:
    from .sensitive_meta import SensitiveMeta # Import model for type hinting

logger = logging.getLogger(__name__)
SECRET_SALT = os.getenv("DJANGO_SALT", "default_salt")
DEFAULT_UNKNOWN_NAME = "unknown"

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
    except Exception:
        pass
    
    try:
        # Try dateparser with German locale preference
        import dateparser
        dt = dateparser.parse(
            s, 
            settings={
                "DATE_ORDER": "DMY", 
                "PREFER_DAY_OF_MONTH": "first"
            }
        )
        return dt.date() if dt else None
    except Exception as e:
        logger.debug(f"Dateparser fallback failed for '{s}': {e}")
        return None


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
    start_date = today - timedelta(days=20 * 365) # Approximate 20 years back
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

    hash_str = get_patient_hash(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center=center.name, # Use center name
        salt=salt,
    )
    return sha256(hash_str.encode()).hexdigest()


def calculate_examination_hash(instance: "SensitiveMeta", salt: str = SECRET_SALT) -> str:
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


    hash_str = get_patient_examination_hash(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        examination_date=examination_date,
        center=center.name, # Use center name
        salt=salt,
    )
    return sha256(hash_str.encode()).hexdigest()


def create_pseudo_examiner_logic(instance: "SensitiveMeta") -> "Examiner":
    """Creates or retrieves the pseudo examiner based on instance data."""
    first_name = instance.examiner_first_name
    last_name = instance.examiner_last_name
    center = instance.center # Should be set before calling save

    if not first_name or not last_name or not center:
        logger.warning(f"Incomplete examiner info for SensitiveMeta (pk={instance.pk}). Using default examiner.")
        # Ensure default center exists or handle appropriately
        try:
            default_center = Center.objects.get_by_natural_key("endoreg_db_demo")
        except Center.DoesNotExist:
            logger.error("Default center 'endoreg_db_demo' not found. Cannot create default examiner.")
            raise ValueError("Default center 'endoreg_db_demo' not found.")

        examiner, _created = Examiner.custom_get_or_create(
            first_name="Unknown", last_name="Unknown", center=default_center
        )
    else:
        examiner, _created = Examiner.custom_get_or_create(
            first_name=first_name, last_name=last_name, center=center
        )

    return examiner


def get_or_create_pseudo_patient_logic(instance: "SensitiveMeta") -> "Patient":
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
    return patient


def get_or_create_pseudo_patient_examination_logic(instance: "SensitiveMeta") -> "PatientExamination":
    """Gets or creates the pseudo patient examination based on instance data."""
    # Ensure necessary fields are set
    if not instance.patient_hash:
         instance.patient_hash = calculate_patient_hash(instance)
    if not instance.examination_hash:
         instance.examination_hash = calculate_examination_hash(instance)

    # Ensure the pseudo patient exists first, as PatientExamination might depend on it
    if not instance.pseudo_patient_id:
        pseudo_patient = get_or_create_pseudo_patient_logic(instance)
        instance.pseudo_patient_id = pseudo_patient.pk # Assign FK directly

    patient_examination, _created = PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
        patient_hash=instance.patient_hash,
        examination_hash=instance.examination_hash,
        # Optionally pass pseudo_patient if the method requires it
        # pseudo_patient=instance.pseudo_patient
    )
    return patient_examination


@transaction.atomic # Ensure all operations within save succeed or fail together
def perform_save_logic(instance: "SensitiveMeta") -> "Examiner":
    """
    Contains the core logic for preparing a SensitiveMeta instance for saving.
    Handles data generation (dates), hash calculation, and linking pseudo-entities.
    Returns the Examiner instance to be linked via M2M after the main save.
    """

    # --- Pre-Save Checks and Data Generation ---

    # 1. Ensure DOB and Examination Date exist
    if not instance.patient_dob:
        logger.debug(f"SensitiveMeta (pk={instance.pk}): Patient DOB missing, generating random.")
        instance.patient_dob = generate_random_dob()
    if not instance.examination_date:
        logger.debug(f"SensitiveMeta (pk={instance.pk}): Examination date missing, generating random.")
        instance.examination_date = generate_random_examination_date()

    # 2. Ensure Center exists (should be set before calling save)
    if not instance.center:
        raise ValueError("Center must be set before saving SensitiveMeta.")

    # 3. Ensure Gender exists (should be set before calling save, e.g., during creation/update)
    if not instance.patient_gender:
         # Attempt to guess if names are available
         first_name = instance.patient_first_name or DEFAULT_UNKNOWN_NAME
         gender = guess_name_gender(first_name)
         if not gender:
             raise ValueError("Patient gender could not be determined and must be set before saving.")
         instance.patient_gender = gender


    # 4. Calculate Hashes (depends on DOB, Exam Date, Center, Names)
    instance.patient_hash = calculate_patient_hash(instance)
    instance.examination_hash = calculate_examination_hash(instance)

    # 5. Get or Create Pseudo Patient (depends on hash, center, gender, dob)
    # Assign directly to the FK field to avoid premature saving issues
    pseudo_patient = get_or_create_pseudo_patient_logic(instance)
    instance.pseudo_patient_id = pseudo_patient.pk

    # 6. Get or Create Pseudo Examination (depends on hashes)
    # Assign directly to the FK field
    pseudo_examination = get_or_create_pseudo_patient_examination_logic(instance)
    instance.pseudo_examination_id = pseudo_examination.pk

    # 7. Get or Create Pseudo Examiner (depends on names, center)
    # This needs to happen *after* the main instance has a PK for M2M linking.
    # We create/get it here and return it to the main save method.
    examiner_instance = create_pseudo_examiner_logic(instance)

    # 8. Ensure SensitiveMetaState exists (will be checked/created *after* main save)

    # Return the examiner instance so the model's save method can handle M2M linking
    return examiner_instance


def create_sensitive_meta_from_dict(cls: Type["SensitiveMeta"], data: Dict[str, Any]) -> "SensitiveMeta":
    """Logic to create a SensitiveMeta instance from a dictionary."""

    field_names = {f.name for f in cls._meta.get_fields() if not f.is_relation or f.one_to_one or (f.many_to_one and f.related_model)}
    selected_data = {k: v for k, v in data.items() if k in field_names}

    # --- Convert patient_dob if it's a date object ---
    dob = selected_data.get("patient_dob")
    if isinstance(dob, date) and not isinstance(dob, datetime):
        # Convert date to datetime at the start of the day and make it timezone-aware
        aware_dob = timezone.make_aware(datetime.combine(dob, datetime.min.time()))
        selected_data["patient_dob"] = aware_dob
        logger.debug("Converted patient_dob from date to aware datetime: %s", aware_dob)
    elif isinstance(dob, str):
        # Handle string DOB - check if it's a field name or actual date
        if dob == "patient_dob" or dob in ["patient_first_name", "patient_last_name", "examination_date"]:
            logger.warning("Skipping invalid patient_dob value '%s' - appears to be field name", dob)
            selected_data.pop("patient_dob", None)  # Remove invalid value
        else:
            # Try to parse as date string
            try:
                import dateparser
                parsed_dob = dateparser.parse(dob, languages=['de'], settings={'DATE_ORDER': 'DMY'})
                if parsed_dob:
                    aware_dob = timezone.make_aware(parsed_dob.replace(hour=0, minute=0, second=0, microsecond=0))
                    selected_data["patient_dob"] = aware_dob
                    logger.debug("Parsed string patient_dob '%s' to aware datetime: %s", dob, aware_dob)
                else:
                    logger.warning("Could not parse patient_dob string '%s', removing from data", dob)
                    selected_data.pop("patient_dob", None)
            except Exception as e:
                logger.warning("Error parsing patient_dob string '%s': %s, removing from data", dob, e)
                selected_data.pop("patient_dob", None)
    # --- End Conversion ---
    
    # Similar validation for examination_date
    exam_date = selected_data.get("examination_date")
    if isinstance(exam_date, str):
        if exam_date == "examination_date" or exam_date in ["patient_first_name", "patient_last_name", "patient_dob"]:
            logger.warning("Skipping invalid examination_date value '%s' - appears to be field name", exam_date)
            selected_data.pop("examination_date", None)
        else:
            # Try to parse as date string
            try:
                # First try simple ISO format for YYYY-MM-DD
                if len(exam_date) == 10 and exam_date.count('-') == 2:
                    try:
                        from datetime import datetime as dt
                        parsed_date = dt.strptime(exam_date, '%Y-%m-%d').date()
                        selected_data["examination_date"] = parsed_date
                        logger.debug("Parsed ISO examination_date '%s' to date: %s", exam_date, parsed_date)
                    except ValueError:
                        # Fall back to dateparser for complex formats
                        import dateparser
                        parsed_date = dateparser.parse(exam_date, languages=['de'], settings={'DATE_ORDER': 'DMY'})
                        if parsed_date:
                            selected_data["examination_date"] = parsed_date.date()
                            logger.debug("Parsed string examination_date '%s' to date: %s", exam_date, parsed_date.date())
                        else:
                            logger.warning("Could not parse examination_date string '%s', removing from data", exam_date)
                            selected_data.pop("examination_date", None)
                else:
                    # Use dateparser for non-ISO formats
                    import dateparser
                    parsed_date = dateparser.parse(exam_date, languages=['de'], settings={'DATE_ORDER': 'DMY'})
                    if parsed_date:
                        selected_data["examination_date"] = parsed_date.date()
                        logger.debug("Parsed string examination_date '%s' to date: %s", exam_date, parsed_date.date())
                    else:
                        logger.warning("Could not parse examination_date string '%s', removing from data", exam_date)
                        selected_data.pop("examination_date", None)
            except Exception as e:
                logger.warning("Error parsing examination_date string '%s': %s, removing from data", exam_date, e)
                selected_data.pop("examination_date", None)

    # Handle Center
    center_name = data.get("center_name")
    if not center_name:
        raise ValueError("center_name is required in data dictionary.")
    try:
        center = Center.objects.get_by_natural_key(center_name)
        selected_data["center"] = center
    except Center.DoesNotExist as exc:
        raise ValueError(f"Center with name '{center_name}' does not exist.") from exc

    # Handle Names and Gender
    first_name = selected_data.get("patient_first_name") or DEFAULT_UNKNOWN_NAME
    last_name = selected_data.get("patient_last_name") or DEFAULT_UNKNOWN_NAME
    selected_data["patient_first_name"] = first_name # Ensure defaults are set
    selected_data["patient_last_name"] = last_name

    patient_gender_input = selected_data.get("patient_gender")

    if isinstance(patient_gender_input, Gender):
        # Already a Gender object, nothing to do
        pass
    elif isinstance(patient_gender_input, str):
        # Input is a string (gender name)
        try:
            selected_data["patient_gender"] = Gender.objects.get(name=patient_gender_input)
        except Gender.DoesNotExist:
            logger.warning(f"Gender with name '{patient_gender_input}' provided but not found. Attempting to guess or use default.")
            # Fall through to guessing logic if provided string name is invalid
            patient_gender_input = None # Reset to trigger guessing
    
    if not isinstance(selected_data.get("patient_gender"), Gender): # If not already a Gender object (e.g. was None, or string lookup failed)
        gender_name_to_use = guess_name_gender(first_name)
        if not gender_name_to_use:
            logger.warning(f"Could not guess gender for name '{first_name}'. Setting Gender to unknown.")
            gender_name_to_use = "unknown"
        try:
            selected_data["patient_gender"] = Gender.objects.get(name=gender_name_to_use)
        except Gender.DoesNotExist:
            # This should ideally not happen if "unknown" gender is guaranteed to exist
            raise ValueError(f"Default or guessed gender '{gender_name_to_use}' does not exist in Gender table.")

    # Update name DB
    update_name_db(first_name, last_name)

    # Instantiate without saving yet
    sensitive_meta = cls(**selected_data)

    # Call save once at the end. This triggers the custom save logic.
    sensitive_meta.save() # This will call perform_save_logic internally

    return sensitive_meta


def update_sensitive_meta_from_dict(instance: "SensitiveMeta", data: Dict[str, Any]) -> "SensitiveMeta":
    """Logic to update a SensitiveMeta instance from a dictionary."""
    field_names = {f.name for f in instance._meta.get_fields() if not f.is_relation or f.one_to_one or (f.many_to_one and f.related_model)}
    # Exclude FKs that should not be updated directly from dict keys (handled separately or via save logic)
    excluded_fields = {'pseudo_patient', 'pseudo_examination'}
    selected_data = {k: v for k, v in data.items() if k in field_names and k not in excluded_fields}

    # Handle potential Center update
    center_name = data.get("center_name")
    if center_name:
        try:
            center = Center.objects.get_by_natural_key(center_name)
            instance.center = center # Update center directly
        except Center.DoesNotExist as exc:
            logger.warning(f"Center '{center_name}' not found during update. Keeping existing center.")
            selected_data.pop('center', None) # Remove from dict if not found

    # Set examiner names if provided, before calling save
    examiner_first_name = data.get("examiner_first_name")
    examiner_last_name = data.get("examiner_last_name")
    if examiner_first_name is not None: # Allow setting empty strings
        instance.examiner_first_name = examiner_first_name
    if examiner_last_name is not None:
        instance.examiner_last_name = examiner_last_name

    # Handle patient_gender specially with graceful error handling
    patient_gender_input = data.get("patient_gender")
    if patient_gender_input is not None:
        try:
            if isinstance(patient_gender_input, Gender):
                selected_data["patient_gender"] = patient_gender_input
            elif isinstance(patient_gender_input, str):
                gender_input_clean = patient_gender_input.strip()
                # Try direct case-insensitive DB lookup first
                gender_obj = Gender.objects.filter(name__iexact=gender_input_clean).first()
                if gender_obj:
                    selected_data["patient_gender"] = gender_obj
                    logger.debug(f"Successfully matched gender string '{patient_gender_input}' to Gender object via iexact lookup")
                else:
                    # Use mapping helper for fallback
                    mapped = _map_gender_string_to_standard(gender_input_clean)
                    if mapped:
                        gender_obj = Gender.objects.filter(name__iexact=mapped).first()
                        if gender_obj:
                            selected_data["patient_gender"] = gender_obj
                            logger.info(f"Mapped gender '{patient_gender_input}' to '{mapped}' via fallback mapping")
                        else:
                            logger.warning(f"Mapped gender '{patient_gender_input}' to '{mapped}', but no such Gender in DB. Trying 'unknown'.")
                            unknown_gender = Gender.objects.filter(name__iexact='unknown').first()
                            if unknown_gender:
                                selected_data["patient_gender"] = unknown_gender
                                logger.warning(f"Using 'unknown' gender as fallback for '{patient_gender_input}'")
                            else:
                                logger.error(f"No 'unknown' gender found in database. Cannot handle gender '{patient_gender_input}'. Skipping gender update.")
                                selected_data.pop("patient_gender", None)
                    else:
                        # Last resort: try to get 'unknown' gender
                        unknown_gender = Gender.objects.filter(name__iexact='unknown').first()
                        if unknown_gender:
                            selected_data["patient_gender"] = unknown_gender
                            logger.warning(f"Using 'unknown' gender as fallback for '{patient_gender_input}' (no mapping)")
                        else:
                            logger.error(f"No 'unknown' gender found in database. Cannot handle gender '{patient_gender_input}'. Skipping gender update.")
                            selected_data.pop("patient_gender", None)
            else:
                logger.warning(f"Unexpected patient_gender type {type(patient_gender_input)}: {patient_gender_input}. Skipping gender update.")
                selected_data.pop("patient_gender", None)
        except Exception as e:
            logger.exception(f"Error handling patient_gender '{patient_gender_input}': {e}. Skipping gender update.")
            selected_data.pop("patient_gender", None)

    # Update other attributes from selected_data
    patient_name_changed = False
    for k, v in selected_data.items():
        # Avoid overwriting examiner names if they were just explicitly set
        if k not in ["examiner_first_name", "examiner_last_name"] or \
           (k == "examiner_first_name" and examiner_first_name is None) or \
           (k == "examiner_last_name" and examiner_last_name is None):

            try:
                # --- Convert patient_dob if it's a date object ---
                value_to_set = v
                if k == "patient_dob":
                    if isinstance(v, date) and not isinstance(v, datetime):
                        aware_dob = timezone.make_aware(datetime.combine(v, datetime.min.time()))
                        value_to_set = aware_dob
                        logger.debug("Converted patient_dob from date to aware datetime during update: %s", aware_dob)
                    elif isinstance(v, str):
                        # Handle string DOB - check if it's a field name or actual date
                        if v == "patient_dob" or v in ["patient_first_name", "patient_last_name", "examination_date"]:
                            logger.warning("Skipping invalid patient_dob value '%s' during update - appears to be field name", v)
                            continue  # Skip this field
                        else:
                            # Try to parse as date string
                            try:
                                import dateparser
                                parsed_dob = dateparser.parse(v, languages=['de'], settings={'DATE_ORDER': 'DMY'})
                                if parsed_dob:
                                    value_to_set = timezone.make_aware(parsed_dob.replace(hour=0, minute=0, second=0, microsecond=0))
                                    logger.debug("Parsed string patient_dob '%s' during update to aware datetime: %s", v, value_to_set)
                                else:
                                    logger.warning("Could not parse patient_dob string '%s' during update, skipping", v)
                                    continue
                            except Exception as e:
                                logger.warning("Error parsing patient_dob string '%s' during update: %s, skipping", v, e)
                                continue
                elif k == "examination_date" and isinstance(v, str):
                    if v == "examination_date" or v in ["patient_first_name", "patient_last_name", "patient_dob"]:
                        logger.warning("Skipping invalid examination_date value '%s' during update - appears to be field name", v)
                        continue
                    else:
                        # Try to parse as date string
                        try:
                            import dateparser
                            parsed_date = dateparser.parse(v, languages=['de'], settings={'DATE_ORDER': 'DMY'})
                            if parsed_date:
                                value_to_set = parsed_date.date()
                                logger.debug("Parsed string examination_date '%s' during update to date: %s", v, value_to_set)
                            else:
                                logger.warning("Could not parse examination_date string '%s' during update, skipping", v)
                                continue
                        except Exception as e:
                            logger.warning("Error parsing examination_date string '%s' during update: %s, skipping", v, e)
                            continue
                # --- End Conversion ---

                # Check if patient name is changing
                if k in ["patient_first_name", "patient_last_name"] and getattr(instance, k) != value_to_set:
                    patient_name_changed = True
                
                setattr(instance, k, value_to_set) # Use value_to_set
                
            except Exception as e:
                logger.error(f"Error setting attribute '{k}' to '{v}': {e}. Skipping this field.")
                continue

    # Update name DB if patient names changed
    if patient_name_changed:
        try:
            update_name_db(instance.patient_first_name, instance.patient_last_name)
        except Exception as e:
            logger.warning(f"Error updating name database: {e}")

    # Call save - this will trigger the full save logic including hash recalculation etc.
    try:
        instance.save()
    except Exception as e:
        logger.error(f"Error saving SensitiveMeta instance: {e}")
        raise

    return instance


def update_or_create_sensitive_meta_from_dict(
    cls: Type["SensitiveMeta"],
    data: Dict[str, Any],
    instance: Optional["SensitiveMeta"] = None
) -> "SensitiveMeta":
    """Logic to update or create a SensitiveMeta instance from a dictionary."""
    # Check if the instance already exists based on unique fields
    if instance:
        # Update the existing instance
        return update_sensitive_meta_from_dict(instance, data), False
    else:
        # Create a new instance
        return create_sensitive_meta_from_dict(cls, data), True


def _map_gender_string_to_standard(gender_str: str) -> Optional[str]:
    """Maps various gender string inputs to standard gender names used in the DB."""
    mapping = {
        'male': ['male', 'm', 'männlich', 'man'],
        'female': ['female', 'f', 'weiblich', 'woman'],
        'unknown': ['unknown', 'unbekannt', 'other', 'diverse', '']
    }
    gender_lower = gender_str.strip().lower()
    for standard, variants in mapping.items():
        if gender_lower in variants:
            return standard
    return None
