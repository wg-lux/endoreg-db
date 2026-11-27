from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0041_quarantineitem"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoHlsArtifact",
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
                (
                    "artifact_kind",
                    models.CharField(
                        choices=[("raw", "Raw"), ("processed", "Processed")],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("materializing", "Materializing"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                        ],
                        default="materializing",
                        max_length=32,
                    ),
                ),
                (
                    "key_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("key_ciphertext", models.BinaryField(blank=True, null=True)),
                ("key_nonce", models.BinaryField(blank=True, null=True)),
                (
                    "key_wrap_algorithm",
                    models.CharField(
                        default="AESGCM-master-wrap-v1",
                        max_length=64,
                    ),
                ),
                ("iv_hex", models.CharField(blank=True, max_length=32)),
                (
                    "playlist_relative_path",
                    models.CharField(blank=True, max_length=500),
                ),
                (
                    "segment_directory_relative_path",
                    models.CharField(blank=True, max_length=500),
                ),
                ("segment_count", models.PositiveIntegerField(default=0)),
                ("source_file_name", models.CharField(blank=True, max_length=500)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "video",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hls_artifacts",
                        to="endoreg_db.videofile",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="videohlsartifact",
            constraint=models.UniqueConstraint(
                fields=("video", "artifact_kind"),
                name="unique_video_hls_artifact_kind",
            ),
        ),
        migrations.AddIndex(
            model_name="videohlsartifact",
            index=models.Index(
                fields=["video", "artifact_kind", "status"],
                name="endoreg_db__video_i_3a06b0_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="videohlsartifact",
            index=models.Index(
                fields=["key_id", "status"],
                name="endoreg_db__key_id_0bfdb5_idx",
            ),
        ),
    ]
