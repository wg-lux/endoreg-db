from endoreg_db.models import SensitiveMeta
import logging
from datetime import timedelta
from typing import Tuple

from django.db.models import QuerySet

from itertools import combinations
from typing import Dict

logger = logging.getLogger(__name__)


QI_FLAGS = ["first_name", "last_name", "center", "gender", "dob_band"]


def get_k_profile_for_instance(
    instance: SensitiveMeta,
    *,
    dob_year_tolerance: int = 1,
    include_self: bool = True,
) -> Dict[Tuple[str, ...], int]:
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
    result: Dict[Tuple[str, ...], int] = {}

    for r in range(1, len(QI_FLAGS) + 1):
        for subset in combinations(QI_FLAGS, r):
            use_first_name = "first_name" in subset
            use_last_name = "last_name" in subset
            use_center = "center" in subset
            use_gender = "gender" in subset
            use_dob_band = "dob_band" in subset

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
            result[subset] = k_value

    return result


def get_k_anonymity(pk, k=3):
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
    qs = SensitiveMeta.objects.all()

    if use_first_name and instance.patient_first_name is not None:
        qs = qs.filter(patient_first_name=instance.patient_first_name)

    if use_last_name and instance.patient_last_name is not None:
        qs = qs.filter(patient_last_name=instance.patient_last_name)
    # --- Center ---
    if use_center and instance.center is not None:
        if instance.center.pk is not None:
            qs = qs.filter(center=instance.center.pk)

    # --- Gender ---
    if use_gender and instance.patient_gender is not None:
        if instance.patient_gender.pk is not None:
            qs = qs.filter(patient_gender_id=instance.patient_gender)

    # --- DOB (approximate ±N years using days) ---
    if use_dob_band and instance.patient_dob is not None:
        days = dob_year_tolerance * 365
        ref_date = instance.patient_dob.date()
        start = ref_date - timedelta(days=days)
        end = ref_date + timedelta(days=days)
        qs = qs.filter(patient_dob__date__range=(start, end))

    # --- Exclude self if requested ---
    if not include_self and instance.pk is not None:
        qs = qs.exclude(pk=instance.pk)

    return qs


def get_k_anonymity_for_sensitive_meta(
    pk: int,
    *,
    k: int = 3,
    dob_year_tolerance: int = 1,
) -> Tuple[int, bool]:
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
    is_k_anon = k_value >= k

    logger.info(
        "k-anonymity for SensitiveMeta pk=%s -> k=%s (threshold=%s, dob_tol=%s years)",
        pk,
        k_value,
        k,
        dob_year_tolerance,
    )

    return k_value, is_k_anon
