# Generated by Django 4.2.11 on 2024-03-24 14:57

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0018_reportreaderflag_reportreaderconfig'),
    ]

    operations = [
        migrations.AddField(
            model_name='pdftype',
            name='cut_off_above_lines',
            field=models.ManyToManyField(related_name='pdf_type_cut_off_above_lines', to='endoreg_db.reportreaderflag'),
        ),
        migrations.AddField(
            model_name='pdftype',
            name='cut_off_below_lines',
            field=models.ManyToManyField(related_name='pdf_type_cut_off_below_lines', to='endoreg_db.reportreaderflag'),
        ),
        migrations.AddField(
            model_name='pdftype',
            name='endoscopy_info_line',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='pdf_type_endoscopy_info_line', to='endoreg_db.reportreaderflag'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pdftype',
            name='examiner_info_line',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='pdf_type_examiner_info_line', to='endoreg_db.reportreaderflag'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pdftype',
            name='patient_info_line',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='pdf_type_patient_info_line', to='endoreg_db.reportreaderflag'),
            preserve_default=False,
        ),
    ]