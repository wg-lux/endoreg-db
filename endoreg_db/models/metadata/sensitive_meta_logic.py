from __future__ import annotations

# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
import logging
import random
import re  # Neu hinzugefügt für Regex-Pattern
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional, Protocol, cast

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from endoreg_db.config.identity_hashing import current_identity_keyring
from endoreg_db.services.secret_rotation.identity import (
    IdentitySource,
    require_identity_center,
    migrate_identity_group,
    salt_fingerprint as rotation_salt_fingerprint,
    legacy_source_matches_ring,
)
from endoreg_db.services.secret_rotation.historical_resolution import (
    resolve_historical_identity,
)

from endoreg_db.utils import guess_name_gender

# Assuming these utils are correctly located
from endoreg_db.utils.hashs import (
    get_patient_examination_hash,
    get_patient_identity_fingerprint,
    get_identity_salt_fingerprint,
)

# Import models needed for logic, use local imports inside functions if needed to break cycles
from ..administration import Center, Examiner, Patient
from ..medical import PatientExamination
from ..other import Gender

if TYPE_CHECKING:
    from .sensitive_meta import SensitiveMeta  # Import model for type hinting


class _GenderManager(Protocol):
    def resolve_by_name(self, name: str) -> Gender | None: ...

    def get_or_create_by_name(
        self, *, name: str, defaults: dict[str, object]
    ) -> tuple[Gender, bool]: ...


logger = logging.getLogger(__name__)
DEFAULT_UNKNOWN = "unknown"

_UNKNOWN_IDENTIFIERS = frozenset({"", "unknown", "none", "null", "n/a", "-"})


def _identity_date(value: date) -> date:
    """Recover the clinical calendar date after DateTimeField UTC persistence."""
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            return timezone.localdate(value, timezone.get_default_timezone())
        return value.date()
    return value


def _has_patient_identity(instance: "SensitiveMeta") -> bool:
    dob = instance.patient_dob
    if isinstance(dob, date):
        dob = _identity_date(dob)
    return (
        all(
            isinstance(value, str)
            and value.strip().casefold() not in _UNKNOWN_IDENTIFIERS
            for value in (instance.patient_first_name, instance.patient_last_name)
        )
        and isinstance(dob, date)
        and dob != date(1900, 1, 1)
    )


def _guard_patient_hash_identity(
    instance: "SensitiveMeta", *, salt: bytes | None = None
) -> None:
    """Reject ambiguous legacy encodings before creating or reusing patient links."""
    if connection.vendor == "postgresql":
        lock_key = int(str(instance.patient_hash)[:16], 16)
        if lock_key >= 2**63:
            lock_key -= 2**64
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])
    salt_fingerprint = get_identity_salt_fingerprint(salt)
    stamped = instance.__class__.objects.filter(center_id=instance.center_id).exclude(
        identity_salt_fingerprint=""
    )
    if connection.vendor == "postgresql" and not stamped.exists():
        # Serialize first enrollment even when workers resolve different patients.
        # Recheck evidence below after a waiter observes the first writer's commit.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [20260921, 1])
    ring = current_identity_keyring()
    accepted_fingerprints = (
        [rotation_salt_fingerprint(value) for value in ring.readers]
        if ring is not None
        else [salt_fingerprint]
    )
    if stamped.exclude(identity_salt_fingerprint__in=accepted_fingerprints).exists():
        raise ValueError(
            "Configured identity salt differs from persisted identity evidence; migration review required"
        )
    if not stamped.exists():
        # Source-scoped external mappings have their own identity encoding and
        # cannot establish whether a legacy demographic hash used this salt.
        legacy = (
            instance.__class__.objects.filter(
                external_id__isnull=True, center_id=instance.center_id
            )
            .exclude(patient_hash__isnull=True)
            .exclude(patient_hash="")
        )
        # Old imports created hashes from provisional OCR/default values before
        # verification. Those rows cannot establish global salt provenance.
        # Retain strict checks for verified/erased identities and the row being
        # updated; an unrelated provisional row must not block a new import.
        evidence = legacy.filter(
            Q(state__names_verified=True, state__dob_verified=True)
            | Q(direct_identifiers_cleared_at__isnull=False)
            | Q(pk=instance.pk)
        )
        for reference in evidence.iterator():
            if not _has_patient_identity(reference):
                raise ValueError(
                    "Legacy identities lack source evidence for salt enrollment; explicit review required"
                )
            if calculate_patient_hash(
                reference
            ) != reference.patient_hash and not legacy_source_matches_ring(reference):
                raise ValueError(
                    "Configured identity salt does not match legacy patient hashes"
                )
    instance.identity_salt_fingerprint = salt_fingerprint
    dob = instance.patient_dob
    dob = _identity_date(dob) if isinstance(dob, date) else dob
    for existing in (
        instance.__class__.objects.filter(patient_hash=instance.patient_hash)
        .exclude(pk=instance.pk)
        .only(
            "patient_first_name",
            "patient_last_name",
            "patient_dob",
            "center_id",
            "direct_identifiers_cleared_at",
            "identity_fingerprint",
        )
    ):
        existing_dob = existing.patient_dob
        existing_dob = (
            _identity_date(existing_dob)
            if isinstance(existing_dob, date)
            else existing_dob
        )
        if existing.identity_fingerprint:
            if existing.identity_fingerprint == instance.identity_fingerprint:
                continue
            raise ValueError(
                "Ambiguous legacy patient identity requires explicit review"
            )
        if existing.direct_identifiers_cleared_at is not None:
            raise ValueError("Erased legacy patient identity requires explicit review")
        if (
            existing.patient_first_name,
            existing.patient_last_name,
            existing_dob,
            existing.center_id,
        ) != (
            instance.patient_first_name,
            instance.patient_last_name,
            dob,
            instance.center_id,
        ):
            raise ValueError(
                "Ambiguous legacy patient identity requires explicit review"
            )


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


def calculate_patient_hash(instance: "SensitiveMeta", salt: str | None = None) -> str:
    """Calculates the patient hash for the instance."""
    dob = instance.patient_dob
    first_name = instance.patient_first_name
    last_name = instance.patient_last_name
    center = instance.center

    if not dob:
        raise ValueError("Patient DOB is required to calculate patient hash.")
    if not center:
        raise ValueError("Center is required to calculate patient hash.")
    dob = _identity_date(dob)

    assert first_name is not None, "First name is required to calculate patient hash."
    assert last_name is not None, "Last name is required to calculate patient hash."

    hash_str = get_patient_identity_fingerprint(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center_key=center.center_key,
        salt=salt,
    )
    return hash_str


def calculate_examination_hash(
    instance: "SensitiveMeta", salt: str | None = None
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
    dob = _identity_date(dob)

    if not first_name:
        raise ValueError("First name is required to calculate examination hash.")
    if not last_name:
        raise ValueError("Last name is required to calculate examination hash.")

    hash_str = get_patient_examination_hash(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        examination_date=examination_date,
        center_key=center.center_key,
        salt=salt,
    )
    return hash_str


def create_pseudo_examiner_logic(instance: "SensitiveMeta") -> "Examiner":
    """Creates or retrieves the pseudo examiner based on instance data."""
    first_name = instance.examiner_first_name
    last_name = instance.examiner_last_name
    center = instance.center  # Should be set before calling save
    if center is not None:
        require_identity_center(center)
    elif current_identity_keyring() is not None:
        raise ValueError("Managed examiner identity requires a persisted center")

    if not first_name or not last_name or not center:
        logger.warning(
            f"Incomplete examiner info for SensitiveMeta (pk={instance.pk or 'new'}). Using default examiner."
        )
        # Ensure default center exists or handle appropriately
        default_center = center
        if default_center is None:
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

    if instance.center is None:
        raise ValueError("Center must be set before resolving a patient examination.")

    # Ensure the pseudo patient exists first, as PatientExamination might depend on it
    if not instance.pseudo_patient:
        pseudo_patient, _created = get_or_create_pseudo_patient_logic(instance)
        instance.pseudo_patient = pseudo_patient  # Assign FK directly

    patient_examination, _created = (
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            patient_hash=instance.patient_hash,
            examination_hash=instance.examination_hash,
            center=instance.center,
        )
    )
    return patient_examination, _created


@transaction.atomic  # Ensure all operations within save succeed or fail together
def perform_save_logic(instance: "SensitiveMeta") -> "Examiner | None":
    """
    Resolve complete identities without manufacturing missing clinical data.

    Incomplete extraction remains available for review without patient or
    examination links. Complete identity input uses canonical UTF-8 HMAC encoding,
    rejects conflicting source identities, and serializes PostgreSQL creation.
    The outer model save keeps these checks and persistence in one transaction.
    """

    # --- Pre-Save Checks and Data Generation ---

    if not instance.center:
        raise ValueError("Center must be set before saving SensitiveMeta.")
    if instance.direct_identifiers_cleared_at is not None:
        # The audited tombstone preserves the committed identity after erasure.
        from endoreg_db.services.privacy.quality_evaluation import (
            require_erased_sensitive_meta,
        )

        require_erased_sensitive_meta(instance)
        return None
    if not _has_patient_identity(instance):
        if instance.external_id is not None:
            patient = instance.external_id.patient
            examination = instance.pseudo_examination
            if (
                instance.pseudo_patient_id != patient.pk
                or instance.center_id != patient.center_id
                or instance.patient_hash != patient.patient_hash
                or not instance.patient_hash
                or (
                    examination is not None
                    and (
                        examination.patient_id != patient.pk
                        or examination.hash != instance.examination_hash
                    )
                )
            ):
                raise ValueError("External patient identity links are inconsistent")
            return create_pseudo_examiner_logic(instance)
        if instance.pk and instance.patient_hash:
            raise ValueError(
                "Incomplete patient metadata cannot replace a committed identity"
            )
        instance.patient_hash = None
        instance.examination_hash = None
        instance.pseudo_patient = None
        instance.pseudo_examination = None
        return create_pseudo_examiner_logic(instance)

    # 3. Ensure Gender exists (should be set before calling save, e.g., during creation/update)
    require_identity_center(instance.center)
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
    # Publish canonical HMACs for complete source data; compatibility is resolved below.
    instance.patient_hash = calculate_patient_hash(instance)
    if not instance.center or not instance.center.center_key:
        raise ValueError("Persisted center is required for patient identity")
    instance.identity_fingerprint = get_patient_identity_fingerprint(
        instance.patient_first_name,
        instance.patient_last_name,
        _identity_date(instance.patient_dob),
        instance.center.center_key,
    )
    instance.examination_hash = (
        calculate_examination_hash(instance) if instance.examination_date else None
    )
    historical_salt = resolve_historical_identity(
        instance,
        IdentitySource(
            first_name=instance.patient_first_name,
            last_name=instance.patient_last_name,
            dob=_identity_date(instance.patient_dob),
            examination_date=instance.examination_date,
        ),
    )
    if historical_salt is None:
        migrate_identity_group(instance)
    _guard_patient_hash_identity(instance, salt=historical_salt)

    # 5. Get or Create Pseudo Patient (depends on hash, center, gender, dob)
    # Assign directly to the FK field to avoid premature saving issues
    pseudo_patient, _created = get_or_create_pseudo_patient_logic(instance)
    instance.pseudo_patient = pseudo_patient

    # 6. Get or Create Pseudo Examination (depends on hashes)
    # Assign directly to the FK field
    if instance.examination_hash:
        pseudo_examination, _created = get_or_create_pseudo_patient_examination_logic(
            instance
        )
        instance.pseudo_examination = pseudo_examination
    else:
        instance.pseudo_examination = None

    # 7. Get or Create Pseudo Examiner (depends on names, center)
    # This needs to happen *after* the main instance has a PK for M2M linking.
    # We create/get it here and return it to the main save method.
    examiner_instance = create_pseudo_examiner_logic(instance)

    # 8. Ensure SensitiveMetaState exists (will be checked/created *after* main save)

    # Return the examiner instance so the model's save method can handle M2M linking
    return examiner_instance


def _create_anonymized_record(instance: "SensitiveMeta") -> None:
    """Erase direct metadata through the shared policy, preserving identity."""
    from endoreg_db.services.privacy.quality_evaluation import (
        clear_sensitive_meta_direct_identifiers,
    )

    clear_sensitive_meta_direct_identifiers(instance)


# Dictionary ingestion moved to endoreg_db.services.patient_identity; these
# public names stay importable here without a module-level service import.
_MOVED_INGESTION_EXPORTS = frozenset(
    {
        "create_sensitive_meta_from_dict",
        "update_sensitive_meta_from_dict",
        "update_or_create_sensitive_meta_from_dict",
    }
)


def __getattr__(name: str) -> object:
    if name in _MOVED_INGESTION_EXPORTS:
        from endoreg_db.services.patient_identity import sensitive_meta_ingestion

        return getattr(sensitive_meta_ingestion, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
