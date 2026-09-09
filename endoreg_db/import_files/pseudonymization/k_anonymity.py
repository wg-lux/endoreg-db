import logging
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Protocol, cast

from django.db.models import QuerySet

from lx_dtypes.models.contracts.pseudonymization import (
    KAnonymityResult,
    QuasiIdentifierField,
    QuasiIdentifierSubset,
)

from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

logger = logging.getLogger(__name__)


QI_FLAGS: tuple[QuasiIdentifierField, ...] = (
    "first_name",
    "last_name",
    "center",
    "gender",
    "dob_band",
)


class _RelatedIdentity(Protocol):
    pk: int | None


class _SensitiveMetaQuasiIdentifierRecord(Protocol):
    pk: int | None
    patient_first_name: str | None
    patient_last_name: str | None
    center: _RelatedIdentity | None
    patient_gender: _RelatedIdentity | None
    patient_dob: datetime | date | None


def get_k_profile_for_instance(
    instance: SensitiveMeta,
    *,
    dob_year_tolerance: int = 1,
    include_self: bool = True,
) -> dict[QuasiIdentifierSubset, int]:
    """
    For a given SensitiveMeta instance, compute k (equivalence class size)
    for all non-empty subsets of the quasi-identifiers defined in QI_FLAGS.

    Returns:
        {
          ('first_name',):  12,
          ('center', 'gender'): 45,
          ('first_name', 'last_name', 'dob_band'): 3,
          ...
        }
    """
    result: dict[QuasiIdentifierSubset, int] = {}

    for r in range(1, len(QI_FLAGS) + 1):
        for qi_subset in combinations(QI_FLAGS, r):
            use_first_name = "first_name" in qi_subset
            use_last_name = "last_name" in qi_subset
            use_center = "center" in qi_subset
            use_gender = "gender" in qi_subset
            use_dob_band = "dob_band" in qi_subset

            qs = _build_sensitive_meta_qi_queryset(
                instance,
                dob_year_tolerance=dob_year_tolerance,
                include_self=include_self,
                use_first_name=use_first_name,
                use_last_name=use_last_name,
                use_center=use_center,
                use_gender=use_gender,
                use_dob_band=use_dob_band,
            )

            k_value = qs.count()
            result[qi_subset] = k_value

    return result


def get_k_anonymity(pk: int, k: int = 3) -> tuple[int, bool]:
    """
    How anonymized is a patient?
    Get the k value for how many patients can be matched to the current patients attributes.

    Args:
        pk (_type_): _description_
        k (int, optional): _description_. Defaults to 3.
    """
    return get_k_anonymity_for_sensitive_meta(pk=pk, k=k, dob_year_tolerance=1)


def _build_sensitive_meta_qi_queryset(
    instance: SensitiveMeta,
    *,
    dob_year_tolerance: int = 1,
    include_self: bool = True,
    use_first_name: bool = True,
    use_last_name: bool = True,
    use_center: bool = True,
    use_gender: bool = True,
    use_dob_band: bool = True,
) -> QuerySet[SensitiveMeta]:
    """
    Build a queryset of SensitiveMeta records that are indistinguishable from
    `instance` on the chosen quasi-identifiers:

        - same center
        - same patient_gender
        - patient_dob within ±dob_year_tolerance years (approx via days)

    Args:
        instance: The SensitiveMeta instance we evaluate.
        dob_year_tolerance: Allowed +- years around patient_dob.
        include_self: Whether to include `instance` itself in the result.

    Returns:
        A Django QuerySet for further aggregation.
    """
    qi_record = cast(_SensitiveMetaQuasiIdentifierRecord, instance)
    qs = SensitiveMeta.objects.all()

    if use_first_name and qi_record.patient_first_name is not None:
        qs = qs.filter(patient_first_name=qi_record.patient_first_name)

    if use_last_name and qi_record.patient_last_name is not None:
        qs = qs.filter(patient_last_name=qi_record.patient_last_name)
    # --- Center ---
    if use_center and qi_record.center is not None:
        center_pk = qi_record.center.pk
        if center_pk is not None:
            qs = qs.filter(center=center_pk)

    # --- Gender ---
    if use_gender and qi_record.patient_gender is not None:
        patient_gender_pk = qi_record.patient_gender.pk
        if patient_gender_pk is not None:
            qs = qs.filter(patient_gender_id=patient_gender_pk)

    # --- DOB (approximate ±N years using days) ---
    if use_dob_band and qi_record.patient_dob is not None:
        days = dob_year_tolerance * 365
        ref_date = (
            qi_record.patient_dob.date()
            if isinstance(qi_record.patient_dob, datetime)
            else qi_record.patient_dob
        )
        start = ref_date - timedelta(days=days)
        end = ref_date + timedelta(days=days)
        qs = qs.filter(patient_dob__date__range=(start, end))

    # --- Exclude self if requested ---
    if not include_self and qi_record.pk is not None:
        qs = qs.exclude(pk=qi_record.pk)

    return qs


def build_sensitive_meta_qi_queryset(
    instance: SensitiveMeta,
    *,
    dob_year_tolerance: int = 1,
    include_self: bool = True,
    use_first_name: bool = True,
    use_last_name: bool = True,
    use_center: bool = True,
    use_gender: bool = True,
    use_dob_band: bool = True,
) -> QuerySet[SensitiveMeta]:
    return _build_sensitive_meta_qi_queryset(
        instance,
        dob_year_tolerance=dob_year_tolerance,
        include_self=include_self,
        use_first_name=use_first_name,
        use_last_name=use_last_name,
        use_center=use_center,
        use_gender=use_gender,
        use_dob_band=use_dob_band,
    )


def get_k_anonymity_for_sensitive_meta(
    pk: int,
    *,
    k: int = 3,
    dob_year_tolerance: int = 1,
) -> tuple[int, bool]:
    """
    Compute the k-anonymity (equivalence class size) for a SensitiveMeta record.

    k-anonymity here is defined as the number of SensitiveMeta rows that share
    the same quasi-identifiers as the given record:

        - center
        - patient_gender
        - patient_dob within ±dob_year_tolerance years (approximate)

    Args:
        pk: Primary key of the SensitiveMeta instance to evaluate.
        k: Desired anonymity threshold (e.g. 3 for 3-anonymity).
        dob_year_tolerance: Allowed age window in years around patient_dob.

    Returns:
        (k_value, is_k_anonymous) where:
            k_value       = size of the equivalence class
            is_k_anonymous = True if k_value >= k
    """
    try:
        sm = SensitiveMeta.objects.get(pk=pk)
    except SensitiveMeta.DoesNotExist:
        raise ValueError(f"SensitiveMeta with pk={pk} does not exist")

    qs = _build_sensitive_meta_qi_queryset(
        sm,
        dob_year_tolerance=dob_year_tolerance,
        include_self=True,
    )

    k_value = qs.count()
    result = KAnonymityResult(
        k_value=k_value,
        is_k_anonymous=k_value >= k,
        threshold=k,
    )

    logger.info(
        "k-anonymity for SensitiveMeta pk=%s -> k=%s (threshold=%s, dob_tol=%s years)",
        pk,
        result.k_value,
        k,
        dob_year_tolerance,
    )

    return result.as_tuple()
