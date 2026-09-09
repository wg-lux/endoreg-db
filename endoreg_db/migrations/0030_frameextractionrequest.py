from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        (
            "endoreg_db",
            "0029_rename_endoreg_db__frame_i_1044f1_idx_endoreg_db__frame_i_54216f_idx_and_more",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="FrameExtractionRequest",
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
                ("frame_number", models.IntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("failure", "Failure"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("task_id", models.CharField(blank=True, max_length=100)),
                ("error_message", models.TextField(blank=True)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "video",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="frame_extraction_requests",
                        to="endoreg_db.videofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Frame Extraction Request",
                "verbose_name_plural": "Frame Extraction Requests",
                "db_table": "frame_extraction_request",
            },
        ),
        migrations.AddIndex(
            model_name="frameextractionrequest",
            index=models.Index(
                fields=["video", "frame_number"],
                name="frame_extra_video_i_f515a6_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="frameextractionrequest",
            index=models.Index(
                fields=["status"],
                name="frame_extra_status_9685be_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="frameextractionrequest",
            index=models.Index(
                fields=["task_id"],
                name="frame_extra_task_id_882a74_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="frameextractionrequest",
            constraint=models.UniqueConstraint(
                fields=("video", "frame_number"),
                name="uniq_frame_extraction_request_video_frame",
            ),
        ),
    ]
