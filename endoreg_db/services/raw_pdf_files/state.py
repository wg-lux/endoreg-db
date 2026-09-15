from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.state.raw_pdf import RawPdfState


def get_or_create_raw_pdf_state(report: "RawPdfFile") -> "RawPdfState":
    """Ensure a RawPdfFile has a persisted RawPdfState and return it."""
    from endoreg_db.models.state.raw_pdf import RawPdfState

    state = report.state
    state_pk = getattr(state, "pk", None)
    if state is not None and state_pk is not None:
        if not RawPdfState.objects.filter(pk=state_pk).exists():
            state = None

    if state is None:
        state = RawPdfState.objects.create()
        report.state = state
        if report.pk:
            report.save(update_fields=["state"])

    return state


def mark_report_sensitive_meta_processed(
    report: "RawPdfFile",
    *,
    save: bool = True,
) -> "RawPdfFile":
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

    sensitive_meta = report.sensitive_meta
    if not isinstance(sensitive_meta, SensitiveMeta):
        raise AttributeError()

    state = get_or_create_raw_pdf_state(report)
    state.mark_sensitive_meta_processed(save=save)
    return report


def mark_report_sensitive_meta_verified(report: "RawPdfFile") -> "RawPdfFile":
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

    sensitive_meta = report.sensitive_meta
    if not isinstance(sensitive_meta, SensitiveMeta):
        raise AttributeError()

    sensitive_meta.mark_dob_verified()
    sensitive_meta.mark_names_verified()
    return report
