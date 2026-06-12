import logging
from datetime import date as Date, datetime
from typing import Protocol, cast

from lx_dtypes.models.contracts.pseudonymization import (
    KPseudonymizationResult,
    QuasiIdentifierSubset,
)

from .k_anonymity import build_sensitive_meta_qi_queryset
from .fake import fake_name_with_similar_dob_and_gender

from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

logger = logging.getLogger(__name__)


class _NamedGender(Protocol):
    name: str


class _MutableSensitiveMetaPseudonymRecord(Protocol):
    pk: int | None
    patient_gender: _NamedGender | None
    patient_dob: datetime | Date | None
    patient_first_name: str
    patient_last_name: str

    def save(self, *, update_fields: list[str]) -> None: ...


def k_pseudonymize(
    instance: SensitiveMeta,
    *,
    k_threshold: int = 3,
    dob_year_tolerance: int = 3,
    qi_subset: QuasiIdentifierSubset | None = None,
    locale: str = "de_DE",
    seed: int | None = None,
    save: bool = True,
) -> tuple[SensitiveMeta, int, bool]:
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
            Reproducible seed.
        save:
            If True, save the instance after pseudonymization.

    Returns:
        (instance, k_value_after, is_k_anonymous_after)
    """

    # --- 1) Compute k for the requested subset BEFORE pseudonymization ---
    if qi_subset is None:
        qi_subset = ("first_name", "last_name", "center", "gender", "dob_band")
    use_first_name = "first_name" in qi_subset
    use_last_name = "last_name" in qi_subset
    use_center = "center" in qi_subset
    use_gender = "gender" in qi_subset
    use_dob_band = "dob_band" in qi_subset
    pseudonym_record = cast(_MutableSensitiveMetaPseudonymRecord, instance)

    qs_before = build_sensitive_meta_qi_queryset(
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
    if pseudonym_record.patient_gender is not None:
        gender_name = pseudonym_record.patient_gender.name
    else:
        # Fallback if gender missing -> bias to 'male' but you can change that
        gender_name = "male"

    # Original DOB as date (fallback to today's date if missing)
    if isinstance(pseudonym_record.patient_dob, datetime):
        orig_dob = pseudonym_record.patient_dob.date()
    elif isinstance(pseudonym_record.patient_dob, Date):
        orig_dob = pseudonym_record.patient_dob
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
    pseudonym_record.patient_first_name = first_name
    pseudonym_record.patient_last_name = last_name
    pseudonym_record.patient_dob = Date(
        fake_dob.year, fake_dob.month, fake_dob.day
    )  # naive is usually fine for DOB

    if save:
        pseudonym_record.save(
            update_fields=["patient_first_name", "patient_last_name", "patient_dob"]
        )

    # --- 3) Recompute k AFTER pseudonymization ---
    qs_after = build_sensitive_meta_qi_queryset(
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
    result = KPseudonymizationResult(
        k_value_after=k_after,
        is_k_anonymous_after=k_after >= k_threshold,
        threshold=k_threshold,
    )

    logger.info(
        "k_pseudonymize: SensitiveMeta pk=%s, subset=%s, k_before=%s, k_after=%s, threshold=%s",
        pseudonym_record.pk,
        qi_subset,
        k_before,
        result.k_value_after,
        k_threshold,
    )

    k_value_after, is_k_anonymous_after = result.as_tuple()
    return instance, k_value_after, is_k_anonymous_after
