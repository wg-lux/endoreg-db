from __future__ import annotations

# pyright: reportPrivateUsage=false
import json
from itertools import product

import pytest
from django.db.models import Value
from rest_framework.renderers import JSONRenderer

from endoreg_db.models.state.anonymization import (
    AnonymizationState,
    anonymization_status_case,
)
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.serializers.hub.transfer_job import TransferJobCreateSerializer
from endoreg_db.services.anonymization import _state_anonymization_status
from endoreg_db.utils import rust_backend


@pytest.mark.django_db
@pytest.mark.parametrize("report", [False, True])
def test_all_state_flags_match_native_sql_transfer_and_polling(report: bool) -> None:
    fields = [
        "processing_error",
        "anonymization_validated",
        "sensitive_meta_processed",
        "anonymized",
        "processing_started",
    ]
    if not report:
        fields += ["frames_extracted", "was_created"]
    anchor = RawPdfState.objects.create()
    for values in product((False, True), repeat=len(fields)):
        flags = dict(zip(fields, values, strict=True))
        state = RawPdfState(**flags) if report else VideoState(**flags)
        # Project incoming flags so contradictory snapshots are checked without
        # bypassing the persisted VideoState consistency constraints.
        sql_status = (
            RawPdfState.objects.filter(pk=anchor.pk)
            .annotate(
                **{f"flags__{field}": Value(value) for field, value in flags.items()},
            )
            .annotate(
                status_value=anonymization_status_case(
                    report=report,
                    relation_prefix="flags",
                )
            )
            .values_list("status_value", flat=True)
            .get()
        )
        transfer_status = (
            TransferJobCreateSerializer._resolve_report_anonymization_status(
                dict(flags)
            )
            if report
            else TransferJobCreateSerializer._resolve_video_anonymization_status(
                dict(flags)
            )
        )
        native_status = state.anonymization_status
        assert sql_status == native_status.value == transfer_status.value
        assert _state_anonymization_status(state) == native_status.value
        assert str(native_status) == native_status.value
        assert json.loads(JSONRenderer().render({"status": native_status})) == {
            "status": native_status.value
        }
        if flags["processing_error"]:
            assert native_status is AnonymizationState.FAILED
        if (
            flags["processing_started"]
            and not any(
                flags[f]
                for f in (
                    "processing_error",
                    "anonymization_validated",
                    "sensitive_meta_processed",
                    "anonymized",
                )
            )
            and (report or flags["frames_extracted"])
        ):
            assert native_status.value == "processing_anonymization"


@pytest.mark.parametrize(
    "binding",
    [
        "_derive_anonymization_status",
        "_derive_report_anonymization_status",
        "_anonymization_status_rules",
    ],
)
def test_native_status_unavailable_fails_closed(
    monkeypatch: pytest.MonkeyPatch, binding: str
) -> None:
    monkeypatch.setattr(rust_backend, binding, None)
    with pytest.raises(RuntimeError, match="unavailable"):
        if binding == "_anonymization_status_rules":
            anonymization_status_case()
        elif binding == "_derive_report_anonymization_status":
            _ = RawPdfState().anonymization_status
        else:
            _ = VideoState().anonymization_status


def test_unknown_anonymization_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        AnonymizationState("AnonymizationState.PROCESSING_ANONYMIZING")
