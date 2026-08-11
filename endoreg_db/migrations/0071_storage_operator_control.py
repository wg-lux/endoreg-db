import django.db.models.deletion
import uuid
from django.db import migrations, models


def create_global_control_state(apps, schema_editor):
    control_state = apps.get_model("endoreg_db", "StorageBalancingControlState")
    control_state.objects.get_or_create(
        singleton_key="global",
        defaults={"is_paused": False, "version": 0},
    )


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0070_storage_reconciliation")]

    operations = [
        migrations.CreateModel(
            name="StorageBalancingControlState",
            fields=[
                (
                    "singleton_key",
                    models.CharField(
                        default="global",
                        editable=False,
                        max_length=32,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("is_paused", models.BooleanField(default=False)),
                ("version", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("singleton_key", "global")),
                        name="storage_balancing_control_singleton",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="StorageOperatorControlReceipt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("pause", "Pause"),
                            ("resume", "Resume"),
                            ("reconcile", "Reconcile"),
                            ("rebalance", "Rebalance"),
                            ("retry", "Retry"),
                        ],
                        max_length=16,
                    ),
                ),
                ("actor", models.CharField(max_length=255)),
                ("reason", models.CharField(max_length=255)),
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("control_version", models.PositiveBigIntegerField()),
                ("paused_from", models.BooleanField(blank=True, null=True)),
                ("paused_to", models.BooleanField(blank=True, null=True)),
                (
                    "retry_from_state",
                    models.CharField(blank=True, default="", max_length=24),
                ),
                (
                    "retry_target_semantics",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "control_state",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="receipts",
                        to="endoreg_db.storagebalancingcontrolstate",
                    ),
                ),
                (
                    "rotation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operator_control_receipts",
                        to="endoreg_db.storagerotation",
                    ),
                ),
                (
                    "source_placement",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operator_control_receipts",
                        to="endoreg_db.storageartifactplacement",
                    ),
                ),
                (
                    "storage_node",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operator_control_receipts",
                        to="endoreg_db.storagenodestate",
                    ),
                ),
                (
                    "work_item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operator_control_receipts",
                        to="endoreg_db.storagebalanceworkitem",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.AddConstraint(
            model_name="storageoperatorcontrolreceipt",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("action__in", ["pause", "resume"]),
                        ("paused_from__isnull", False),
                        ("paused_to__isnull", False),
                        ("retry_from_state", ""),
                        ("retry_target_semantics", ""),
                        ("rotation__isnull", True),
                        ("source_placement__isnull", True),
                        ("storage_node__isnull", True),
                        ("work_item__isnull", True),
                    )
                    | models.Q(
                        ("action__in", ["reconcile", "rebalance"]),
                        ("paused_from__isnull", True),
                        ("paused_to__isnull", True),
                        ("retry_from_state", ""),
                        ("retry_target_semantics", ""),
                        ("rotation__isnull", True),
                        ("source_placement__isnull", True),
                        ("work_item__isnull", True),
                    )
                    | models.Q(
                        ("action", "retry"),
                        ("paused_from__isnull", True),
                        ("paused_to__isnull", True),
                        ("retry_from_state", "failed"),
                        ("retry_target_semantics", "fresh_placement_required"),
                        ("rotation__isnull", False),
                        ("source_placement__isnull", False),
                        ("storage_node__isnull", True),
                        ("work_item__isnull", False),
                    )
                ),
                name="storage_operator_control_payload_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="storageoperatorcontrolreceipt",
            constraint=models.UniqueConstraint(
                condition=models.Q(("action", "retry")),
                fields=("work_item",),
                name="unique_storage_retry_intent_per_work",
            ),
        ),
        migrations.RunPython(create_global_control_state, migrations.RunPython.noop),
    ]
