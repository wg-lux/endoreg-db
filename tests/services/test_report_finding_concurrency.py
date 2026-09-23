"""PostgreSQL evidence for repeated, concurrent lesion submissions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import connection, connections, transaction

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.services.report_finding_sync import sync_report_findings


def _submit(
    examination_id: int,
    finding_id: int,
    instance_ids: tuple[UUID, UUID],
    barrier: Barrier,
) -> list[int]:
    connections.close_all()
    try:
        examination = PatientExamination.objects.get(pk=examination_id)
        barrier.wait(timeout=20)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s'")
            sync_report_findings(
                examination,
                [
                    {"finding_id": finding_id, "instance_id": str(instance_id)}
                    for instance_id in instance_ids
                ],
                user=None,
            )
        return list(
            PatientFinding.objects.filter(patient_examination=examination)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
def test_concurrent_replays_keep_two_distinct_polyp_instances() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row-lock evidence")
    patient = Patient.objects.create(patient_hash="concurrent-polyp-patient")
    examination = PatientExamination.objects.create(patient=patient)
    finding = Finding.objects.create(name="concurrent_polyp")
    instance_ids = (uuid4(), uuid4())
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_submit, examination.pk, finding.pk, instance_ids, barrier)
            for _ in range(2)
        ]
        results = [future.result(timeout=30) for future in futures]
    assert results[0] == results[1]
    assert len(results[0]) == 2
    assert set(PatientFinding.objects.values_list("instance_id", flat=True)) == set(
        instance_ids
    )
