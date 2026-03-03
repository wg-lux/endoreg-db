from django.db import migrations, models


def deduplicate_image_classification_annotations(apps, schema_editor):
    image_classification_annotation = apps.get_model(
        "endoreg_db", "ImageClassificationAnnotation"
    )
    db_alias = schema_editor.connection.alias

    duplicate_groups = (
        image_classification_annotation.objects.using(db_alias)
        .values("frame_id", "label_id", "information_source_id", "annotator")
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
    )

    for group in duplicate_groups.iterator():
        group_filters = {
            "frame_id": group["frame_id"],
            "label_id": group["label_id"],
            "information_source_id": group["information_source_id"],
            "annotator": group["annotator"],
        }
        group_qs = image_classification_annotation.objects.using(db_alias).filter(
            **group_filters
        )
        keep_id = (
            group_qs.order_by("-date_modified", "-id")
            .values_list("id", flat=True)
            .first()
        )
        if keep_id is None:
            continue
        group_qs.exclude(id=keep_id).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0007_pdfprocessinghistory"),
    ]

    operations = [
        migrations.AddField(
            model_name="imageclassificationannotation",
            name="external_annotation_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=255,
                null=True,
            ),
        ),
        migrations.RunPython(
            deduplicate_image_classification_annotations,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="imageclassificationannotation",
            constraint=models.UniqueConstraint(
                fields=("frame", "label", "information_source", "annotator"),
                name="uniq_frame_label_source_annotator",
            ),
        ),
    ]
