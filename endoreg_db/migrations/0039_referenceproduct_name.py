# Generated by Django 4.2.11 on 2024-06-09 07:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0038_emissionfactor_material_product_productgroup_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='referenceproduct',
            name='name',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
    ]
