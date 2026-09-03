from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0057_patientexamination_multiple_documents"),
    ]

    operations = [
        migrations.CreateModel(
            name="MedicalLedgerWriteReceipt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=255)),
                ("request_hash", models.CharField(max_length=64)),
                ("record_ids", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="medical_ledger_write_receipts",
                        to="endoreg_db.patient",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("patient", "idempotency_key"),
                        name="medled_receipt_patient_key_uq",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(idempotency_key=""),
                        name="medled_receipt_key_nonempty",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(request_hash=""),
                        name="medled_receipt_hash_nonempty",
                    ),
                ],
            },
        ),
    ]
