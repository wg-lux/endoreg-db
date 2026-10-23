from django.db import migrations, models


def copy_examiner_centers(apps, schema_editor):
    del schema_editor
    PortalUserInfo = apps.get_model("endoreg_db", "PortalUserInfo")
    through_model = PortalUserInfo.centers.through
    memberships = []
    for portal_info in PortalUserInfo.objects.exclude(
        examiner__center_id__isnull=True
    ).select_related("examiner"):
        memberships.append(
            through_model(
                portaluserinfo_id=portal_info.pk,
                center_id=portal_info.examiner.center_id,
            )
        )
    through_model.objects.bulk_create(memberships, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0050_frame_presentation_timestamp")]

    operations = [
        migrations.AddField(
            model_name="portaluserinfo",
            name="centers",
            field=models.ManyToManyField(
                blank=True,
                related_name="authorized_portal_user_infos",
                to="endoreg_db.center",
            ),
        ),
        migrations.RunPython(copy_examiner_centers, migrations.RunPython.noop),
    ]
