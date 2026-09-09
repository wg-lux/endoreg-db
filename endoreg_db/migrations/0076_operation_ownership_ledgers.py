from django.db import migrations, models


def normalize_unfenced_training_runs(apps, schema_editor):
    training_run = apps.get_model("endoreg_db", "AIModelTrainingRun")
    training_run.objects.filter(status="running").update(
        status="lost",
        error=(
            "Pre-fencing training run had no durable ownership receipt and was "
            "marked lost during lifecycle migration."
        ),
    )


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0075_alter_modelmeta_weights")]

    operations = [
        migrations.AlterField(
            model_name="aimodeltrainingrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("retry_wait", "Retry wait"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("lost", "Lost"),
                ],
                db_index=True,
                default="queued",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="attempt_id",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="dispatch_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="fencing_token",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="heartbeat_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="lease_expires_at",
            field=models.DateTimeField(
                blank=True, db_index=True, editable=False, null=True
            ),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="max_retries",
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="next_retry_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="owner_id",
            field=models.CharField(
                blank=True, default="", editable=False, max_length=255
            ),
        ),
        migrations.AddField(
            model_name="aimodeltrainingrun",
            name="retry_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(
            normalize_unfenced_training_runs,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="aimodeltrainingrun",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="running",
                        attempt_id__isnull=False,
                        heartbeat_at__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    & ~models.Q(owner_id="")
                )
                | (
                    ~models.Q(status="running")
                    & models.Q(
                        attempt_id__isnull=True,
                        owner_id="",
                        heartbeat_at__isnull=True,
                        lease_expires_at__isnull=True,
                    )
                ),
                name="aid_train_lease_state_consistent",
            ),
        ),
        migrations.AlterField(
            model_name="reportimportattempt",
            name="status",
            field=models.CharField(
                choices=[
                    ("idle", "Idle"),
                    ("active", "Active"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("lost", "Lost"),
                ],
                default="idle",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="transferjob",
            name="transfer_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("claimed", "Claimed"),
                    ("running", "Running"),
                    ("retry_wait", "Retry wait"),
                    ("awaiting_media", "Awaiting Media"),
                    ("applied", "Applied"),
                    ("failed", "Failed"),
                    ("inconsistent", "Inconsistent"),
                    ("lost", "Lost"),
                ],
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="transferjob",
            name="attempt_id",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="transferjob",
            name="operation_candidate_name",
            field=models.CharField(
                blank=True, default="", editable=False, max_length=1024
            ),
        ),
        migrations.AddField(
            model_name="transferjob",
            name="operation_fencing_token",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="transferjob",
            name="operation_heartbeat_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="transferjob",
            name="operation_lease_expires_at",
            field=models.DateTimeField(
                blank=True, db_index=True, editable=False, null=True
            ),
        ),
        migrations.AddField(
            model_name="transferjob",
            name="operation_owner",
            field=models.CharField(
                blank=True, default="", editable=False, max_length=255
            ),
        ),
        migrations.AddConstraint(
            model_name="transferjob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        transfer_status="running",
                        attempt_id__isnull=False,
                        operation_heartbeat_at__isnull=False,
                        operation_lease_expires_at__isnull=False,
                    )
                    & ~models.Q(operation_owner="")
                )
                | (
                    ~models.Q(transfer_status="running")
                    & models.Q(
                        attempt_id__isnull=True,
                        operation_owner="",
                        operation_heartbeat_at__isnull=True,
                        operation_lease_expires_at__isnull=True,
                    )
                ),
                name="transfer_operation_lease_consistent",
            ),
        ),
    ]
