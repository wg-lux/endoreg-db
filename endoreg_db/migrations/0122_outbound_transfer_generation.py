from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.core.validators import RegexValidator
from lx_dtypes.models.contracts.hub_media_envelope import HubMediaEnvelopeReceipt


def backfill_receipted_generations(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    jobs = apps.get_model("endoreg_db", "OutboundHubTransferJob")
    for job in (
        jobs.objects.using(schema_editor.connection.alias)
        .exclude(envelope_receipt=None)
        .select_related("source_center", "target_node", "video_file", "raw_pdf_file")
        .iterator()
    ):
        try:
            receipt = HubMediaEnvelopeReceipt.model_validate(job.envelope_receipt)
        except ValueError:
            continue
        resource_hash = (
            job.video_file.raw_video_hash
            if job.video_file_id
            else job.raw_pdf_file.pdf_hash
        )
        if (
            receipt.transfer_key != job.transfer_key
            or receipt.receiver_transfer_id != job.remote_transfer_id
            or receipt.resource_kind != job.resource_kind
            or receipt.resource_hash != resource_hash
            or receipt.target_node_key != job.target_node.node_key
            or job.source_center_id is None
            or receipt.source_center_key != job.source_center.center_key
            or receipt.plaintext_sha256 != receipt.processed_media_hash
        ):
            continue
        jobs.objects.using(schema_editor.connection.alias).filter(pk=job.pk).update(
            processed_media_hash=receipt.processed_media_hash,
            source_node_key=receipt.source_node_key,
        )


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0121_electrosurgical_activation_log")]

    operations = [
        migrations.AddField(
            model_name="outboundhubtransferjob",
            name="processed_media_hash",
            field=models.CharField(
                max_length=64,
                blank=True,
                default="",
                validators=[RegexValidator(r"^[0-9a-f]{64}$")],
            ),
        ),
        migrations.AddField(
            model_name="outboundhubtransferjob",
            name="source_node_key",
            field=models.CharField(max_length=255, blank=True, default=""),
        ),
        migrations.RunPython(backfill_receipted_generations, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="outboundhubtransferjob",
            name="endoreg_outbound_hub_transfer_unique_video_target_mode",
        ),
        migrations.RemoveConstraint(
            model_name="outboundhubtransferjob",
            name="endoreg_outbound_hub_transfer_unique_report_target_mode",
        ),
        migrations.AddConstraint(
            model_name="outboundhubtransferjob",
            constraint=models.UniqueConstraint(
                fields=[
                    "video_file",
                    "target_node",
                    "transfer_mode",
                    "processed_media_hash",
                ],
                name="endoreg_outbound_video_generation",
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundhubtransferjob",
            constraint=models.UniqueConstraint(
                fields=[
                    "raw_pdf_file",
                    "target_node",
                    "transfer_mode",
                    "processed_media_hash",
                ],
                name="endoreg_outbound_report_generation",
            ),
        ),
    ]
