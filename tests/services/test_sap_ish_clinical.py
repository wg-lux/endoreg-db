from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

from endoreg_db.models import (
    Case,
    Center,
    Patient,
    PatientDisease,
    PatientExternalID,
    PatientLabSample,
    PatientLabValue,
    PatientMedication,
    PatientMedicationSchedule,
)
from endoreg_db.services.sap_ish_clinical import persist_sap_ish_clinical_rows
from endoreg_db.services.sap_ish_import import (
    SapIshNormalizedRow,
    sap_ish_external_id_origin,
)


def _row(
    document_type: str,
    canonical_row: dict[str, object],
    *,
    row_number: int,
) -> SapIshNormalizedRow:
    return SapIshNormalizedRow(
        document_type=document_type,
        canonical_row=canonical_row,
        raw_columns={},
        unknown_columns={},
        source_path=Path(f"{document_type}.txt"),
        row_number=row_number,
    )


@pytest.mark.django_db
def test_structured_import_is_idempotent_and_keeps_meona_patient_level() -> None:
    center = Center.objects.create(name=f"sap-clinical-{uuid4().hex}")
    patient = Patient.objects.create(
        first_name="SAP",
        last_name="Patient",
        patient_hash=f"sap-patient-{uuid4().hex}",
        center=center,
    )
    source_system = "sap_ish_test"
    origin = sap_ish_external_id_origin(
        source_system=source_system,
        center_key=str(center.center_key),
    )
    PatientExternalID.objects.create(
        patient=patient,
        origin=origin,
        external_id="2001",
    )
    rows = (
        _row(
            "diagnosen",
            {
                "patient_nr": "2001",
                "fall_nr": "3001",
                "diagnoseschluessel_1": "K52.9",
                "diagnosezeit": datetime(2024, 5, 16, 8, 0),
            },
            row_number=2,
        ),
        _row(
            "labor",
            {
                "patient_nr": "2001",
                "fall_nr": "3001",
                "dokumentzeit": datetime(2024, 5, 17, 6, 45),
                "leistung": "CRP",
                "leistungstext": "C-reactive protein",
                "messwert": "4,2",
            },
            row_number=2,
        ),
        _row(
            "meona_medikamente",
            {
                "patient_nr": "2001",
                "source_patient_id": "2001",
                "medication_row_id": "med-1",
                "tradename": "Metamizol",
                "apply_date": datetime(2024, 5, 17, 7, 0),
                "actual_dose": "500",
                "unit_dose_name": "mg",
                "status": "given",
            },
            row_number=2,
        ),
    )

    first = persist_sap_ish_clinical_rows(
        rows=rows,
        source_system=source_system,
        center=center,
    )
    replayed_rows = (*rows[:2], replace(rows[2], row_number=99))
    second = persist_sap_ish_clinical_rows(
        rows=replayed_rows,
        source_system=source_system,
        center=center,
    )

    assert first.rows_seen == 3
    assert first.rows_skipped == 0
    assert first.diseases_created == 1
    assert first.lab_values_created == 1
    assert first.medications_created == 1
    assert first.patient_level_medications == 1
    assert second.diseases_reused == 1
    assert second.lab_values_reused == 1
    assert second.medications_reused == 1

    assert PatientDisease.objects.filter(patient=patient).count() == 1
    assert PatientLabSample.objects.filter(patient=patient).count() == 1
    assert PatientLabValue.objects.filter(patient=patient).count() == 1
    assert PatientMedication.objects.filter(patient=patient).count() == 1
    assert PatientMedicationSchedule.objects.filter(patient=patient).count() == 0

    disease = PatientDisease.objects.get(patient=patient)
    lab_value = PatientLabValue.objects.get(patient=patient)
    patient_medication = PatientMedication.objects.get(patient=patient)
    assert disease.disease.name == "sap_ish:diagnosis:K52.9"
    assert lab_value.lab_value.name == "sap_ish:lab:CRP"
    assert lab_value.value == 4.2
    assert patient_medication.medication.name == "sap_ish:medication:Metamizol"
    assert patient_medication.dosage["sap_ish_source_id"]

    case = Case.objects.get(patient=patient)
    assert case.hash and case.hash.startswith("sap_ish:v1:")
    assert list(case.patient_lab_samples.all()) == [lab_value.sample]
    assert list(case.patient_lab_values.all()) == [lab_value]
    assert case.patient_medications.count() == 0
