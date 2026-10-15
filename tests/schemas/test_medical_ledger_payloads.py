from __future__ import annotations

from endoreg_db.schemas.medical_ledger import MedicalLedgerRecordIds


def test_medical_ledger_record_id_defaults_are_instance_local() -> None:
    first = MedicalLedgerRecordIds()
    second = MedicalLedgerRecordIds()

    assert first.diseases is not second.diseases
    assert first.events is not second.events
    assert first.lab_samples is not second.lab_samples
    assert first.lab_values is not second.lab_values
    assert first.medications is not second.medications
    assert first.medication_schedules is not second.medication_schedules

    first.diseases.append(7)
    first.medication_schedules.append(11)

    assert second == MedicalLedgerRecordIds()
