import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0023_copy_legacy_streamable_relative_path"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLedger",
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
                    "ts",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                ("object_type", models.CharField(max_length=80)),
                ("object_pk", models.CharField(max_length=40)),
                ("action", models.CharField(max_length=40)),
                ("data", models.JSONField()),
                ("prev_hash", models.CharField(editable=False, max_length=64)),
                ("hash", models.CharField(editable=False, max_length=64)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["ts"],
                "indexes": [
                    models.Index(
                        fields=["object_type", "object_pk"],
                        name="endoreg_db__object__41eca6_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="LedgerHead",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "current_hash",
                    models.CharField(default="0" * 64, editable=False, max_length=64),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "last_entry",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="endoreg_db.auditledger",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ledger Head",
                "verbose_name_plural": "Ledger Heads",
            },
        ),
    ]
