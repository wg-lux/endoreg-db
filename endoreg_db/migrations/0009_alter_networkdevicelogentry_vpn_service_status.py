# Generated by Django 5.1.1 on 2024-10-12 16:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0008_networkdevicelogentry_aglnet_ip_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='networkdevicelogentry',
            name='vpn_service_status',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]