# from endoreg_db.models import (
#     RawPdfFile,Center,
# )
    
# from datetime import datetime

# from django.core.management import call_command
# from django.test import TestCase
# from io import StringIO
# from .conf import (
#     TEST_GASTRO_REPORT_PATH,
#     TEST_GASTRO_REPORT_RESULTS,
#     TEST_CENTER_NAME,
#     TEST_GASTRO_REPORT_OUTPUT_PATH,
#     check_file_exists,
#     TEST_SALT
# )

# from pathlib import Path


# class TestGastroReport(TestCase):
#     def setUp(self):
#         out = StringIO()
#         call_command("load_gender_data", stdout=out)
#         call_command("load_unit_data", stdout=out)
#         call_command("load_name_data", stdout=out)
#         call_command("load_report_reader_flag_data", stdout=out)
#         call_command("load_pdf_type_data", stdout=out)
#         call_command("load_center_data", stdout=out)
#         call_command("load_endoscope_data", stdout=out)
#         call_command("load_green_endoscopy_wuerzburg_data", stdout=out)

#     def test_gastro_pdf(self):
#         from endoreg_db.models import SensitiveMeta
#         center = Center.objects.get(name=TEST_CENTER_NAME)

#         with open(TEST_GASTRO_REPORT_OUTPUT_PATH, "w") as f:

#             if check_file_exists(TEST_GASTRO_REPORT_PATH):
#                 raw_pdf = RawPdfFile.create_from_file(
#                     file_path=TEST_GASTRO_REPORT_PATH,
#                     center_name = center.name,
#                     # destination_dir=SENSITIVE_DATA_DIR,
#                     delete_source=False,
#                 )

#                 # verify the raw pdf file
#                 pdf_filepath = Path(raw_pdf.file.path)
#                 self.assertTrue(pdf_filepath.exists())
#                 self.assertTrue(pdf_filepath.is_file())

#                 report_reader_config = raw_pdf.get_report_reader_config()
#                 f.write("Report Reader Config:\n")
#                 for key, value in report_reader_config.items():
#                     f.write(f"{key}:\n")
#                     f.write(f"\t{value}")
#                     f.write("\n")
#                 f.write("\n")


#                 # get report_reader_config
#                 text, anonymized_text, report_meta = raw_pdf.process_file(verbose=True)

#                 f.write(f"Anonymized Text:\n{anonymized_text}\n")

#                 sensitive_meta: SensitiveMeta = raw_pdf.sensitive_meta
#                 patient_hash = sensitive_meta.get_patient_hash(salt=TEST_SALT)
#                 patient_examination_hash = sensitive_meta.get_patient_examination_hash(salt=TEST_SALT)

#                 self.assertEqual(patient_hash, TEST_GASTRO_REPORT_RESULTS["patient_hash"])
#                 self.assertEqual(patient_examination_hash, TEST_GASTRO_REPORT_RESULTS["patient_examination_hash"])
                
#                 f.write("Extracted Sensitive Report Meta:\n")
#                 for key, value in report_meta.items():
#                     f.write(f"{key}:\n{value}\n\n")

#                 f.write("Sensitive Meta:\n")
#                 for key, value in sensitive_meta.__dict__.items():
#                     f.write(f"{key}:\n{value}\n\n")

#                 # delete raw pdf file
#                 raw_pdf.delete_with_file()

#                 # verify the raw pdf file is deleted
#                 self.assertFalse(pdf_filepath.exists())


        

