from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0026_videostate_ready_for_export"),
    ]

    operations = [
        migrations.CreateModel(
            name="FrameBoxAnnotation",
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
                ("x", models.FloatField()),
                ("y", models.FloatField()),
                ("width", models.FloatField()),
                ("height", models.FloatField()),
                ("image_width", models.PositiveIntegerField()),
                ("image_height", models.PositiveIntegerField()),
                ("value", models.BooleanField(default=True)),
                ("float_value", models.FloatField(blank=True, null=True)),
                (
                    "annotator",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                (
                    "external_annotation_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=255,
                        null=True,
                    ),
                ),
                ("date_created", models.DateTimeField(auto_now_add=True)),
                ("date_modified", models.DateTimeField(auto_now=True)),
                (
                    "frame",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="box_annotations",
                        to="endoreg_db.frame",
                    ),
                ),
                (
                    "information_source",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="frame_box_annotations",
                        to="endoreg_db.informationsource",
                    ),
                ),
                (
                    "label",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="frame_box_annotations",
                        to="endoreg_db.label",
                    ),
                ),
                (
                    "model_meta",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="frame_box_annotations",
                        to="endoreg_db.modelmeta",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["frame", "label"],
                        name="endoreg_db__frame_i_1044f1_idx",
                    ),
                    models.Index(
                        fields=["frame", "information_source", "annotator"],
                        name="endoreg_db__frame_i_397b7f_idx",
                    ),
                    models.Index(
                        fields=["external_annotation_id"],
                        name="endoreg_db__extern_9d1f58_idx",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="frameboxannotation",
            constraint=models.CheckConstraint(
                condition=models.Q(("x__gte", 0)),
                name="frame_box_x_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="frameboxannotation",
            constraint=models.CheckConstraint(
                condition=models.Q(("y__gte", 0)),
                name="frame_box_y_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="frameboxannotation",
            constraint=models.CheckConstraint(
                condition=models.Q(("width__gt", 0)),
                name="frame_box_width_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="frameboxannotation",
            constraint=models.CheckConstraint(
                condition=models.Q(("height__gt", 0)),
                name="frame_box_height_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="frameboxannotation",
            constraint=models.CheckConstraint(
                condition=models.Q(("image_width__gt", 0)),
                name="frame_box_image_width_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="frameboxannotation",
            constraint=models.CheckConstraint(
                condition=models.Q(("image_height__gt", 0)),
                name="frame_box_image_height_positive",
            ),
        ),
    ]
