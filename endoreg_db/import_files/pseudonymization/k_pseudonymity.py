from typing import Optional, Tuple
import logging

from datetime import date as Date

from .k_anonymity import _build_sensitive_meta_qi_queryset
from .fake import fake_name_with_similar_dob_and_gender

from endoreg_db.models import SensitiveMeta

logger = logging.getLogger(__name__)


def k_pseudonymize(
    instance: SensitiveMeta,
    *,
    k_threshold: int = 3,
    dob_year_tolerance: int = 3,
    qi_subset: Optional[Tuple[str, ...]] = None,
    locale: str = "de_DE",
    seed: Optional[int] = None,
    save: bool = True,
) -> Tuple[SensitiveMeta, int, bool]:
    """
    Ensure a SensitiveMeta instance reaches at least `k_threshold` anonymity
    for the given quasi-identifier subset by pseudonymizing patient
    first_name, last_name and DOB if necessary.

    Args:
        instance:
            The SensitiveMeta instance to process.
        k_threshold:
            Minimal k for the chosen QI subset.
        dob_year_tolerance:
            Used both for k-anonymity DOB band and for Faker's DOB perturbation.
        qi_subset:
            Which QIs to use for k-anonymity check.
            Elements from: {"first_name", "last_name", "center", "gender", "dob_band"}.
            Default = all of them.
        locale:
            Faker locale for a realistic name.
        seed:
            Optional seed for reproducibility.
        save:
            If True, save the instance after pseudonymization.

    Returns:
        (instance, k_value_after, is_k_anonymous_after)
    """

    # --- 1) Compute k for the requested subset BEFORE pseudonymization ---
    if qi_subset is None:
        qi_subset = ("first_name", "last_name", "center", "gender", "dob_band")
        # --- 1) Compute k for the requested subset BEFORE pseudonymization ---
        use_first_name = "first_name" in qi_subset
        use_last_name = "last_name" in qi_subset
        use_center = "center" in qi_subset
        use_gender = "gender" in qi_subset
        use_dob_band = "dob_band" in qi_subset
    use_first_name = "first_name" in qi_subset
    use_last_name = "last_name" in qi_subset
    use_center = "center" in qi_subset
    use_gender = "gender" in qi_subset
    use_dob_band = "dob_band" in qi_subset

    qs_before = _build_sensitive_meta_qi_queryset(
        instance,
        dob_year_tolerance=dob_year_tolerance,
        include_self=True,
        use_first_name=use_first_name,
        use_last_name=use_last_name,
        use_center=use_center,
        use_gender=use_gender,
        use_dob_band=use_dob_band,
    )
    k_before = qs_before.count()

    if k_before >= k_threshold:
        # Already sufficiently anonymous, nothing to do
        return instance, k_before, True

    # --- 2) Pseudonymize name + DOB using Faker ---
    # Gender string for Faker
    if instance.patient_gender and getattr(instance.patient_gender, "name", None):
        gender_name = instance.patient_gender.name
    else:
        # Fallback if gender missing -> bias to 'male' but you can change that
        gender_name = "male"

    # Original DOB as date (fallback to today's date if missing)
    if instance.patient_dob is not None:
        orig_dob: Date = instance.patient_dob.date()
    else:
        orig_dob = Date.today()

    first_name, last_name, fake_dob = fake_name_with_similar_dob_and_gender(
        gender=gender_name,
        dob=orig_dob,
        year_tolerance=dob_year_tolerance,
        locale=locale,
        seed=seed,
    )

    # Assign to instance (SensitiveMeta.patient_dob is a DateTimeField)
    instance.patient_first_name = first_name
    instance.patient_last_name = last_name
    instance.patient_dob = Date(
        fake_dob.year, fake_dob.month, fake_dob.day
    )  # naive is usually fine for DOB

    if save:
        instance.save(
            update_fields=["patient_first_name", "patient_last_name", "patient_dob"]
        )

    # --- 3) Recompute k AFTER pseudonymization ---
    qs_after = _build_sensitive_meta_qi_queryset(
        instance,
        dob_year_tolerance=dob_year_tolerance,
        include_self=True,
        use_first_name=use_first_name,
        use_last_name=use_last_name,
        use_center=use_center,
        use_gender=use_gender,
        use_dob_band=use_dob_band,
    )
    k_after = qs_after.count()
    is_k_anon_after = k_after >= k_threshold

    logger.info(
        "k_pseudonymize: SensitiveMeta pk=%s, subset=%s, k_before=%s, k_after=%s, threshold=%s",
        instance.pk,
        qi_subset,
        k_before,
        k_after,
        k_threshold,
    )

    return instance, k_after, is_k_anon_after
