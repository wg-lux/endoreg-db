from __future__ import annotations

from lx_dtypes.models.contracts.pseudonymization import QuasiIdentifierSubset

from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta


class UnsafeLegacyPseudonymizationError(RuntimeError):
    """Raised when code attempts the retired mutation-based pseudonymizer."""


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
    Reject the retired Faker-based mutation of real clinical metadata.

    The old implementation changed a real row's name and date of birth and then
    treated a record-local count as evidence of k-pseudonymity. That operation
    cannot satisfy the paper's immutable-source-table and release-predicate
    invariants. Call ``services.k_pseudonymity.build_k_pseudonymous_release``
    with a governed de-identified release table instead.
    """

    del instance
    del k_threshold
    del dob_year_tolerance
    del qi_subset
    del locale
    del seed
    del save
    raise UnsafeLegacyPseudonymizationError(
        "Mutation-based SensitiveMeta pseudonymization is disabled. Build a "
        "separate governed release view with build_k_pseudonymous_release."
    )


__all__ = ["UnsafeLegacyPseudonymizationError", "k_pseudonymize"]
