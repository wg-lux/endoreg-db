from pathlib import Path


TEST_REPORT_DIR = Path("test_reports")

if not TEST_REPORT_DIR.exists():
    TEST_REPORT_DIR.mkdir()


TEST_DATALOADER_OUTPUT_PATH = TEST_REPORT_DIR / "dataloader.txt"
TEST_PATIENT_OUTPUT_PATH = TEST_REPORT_DIR / "patients.txt"
TEST_PATIENT_EXAMINATION_OUTPUT_PATH = TEST_REPORT_DIR / "patient_examinations.txt"
TEST_PATIENT_FINDINGS_OUTPUT_PATH = TEST_REPORT_DIR / "patient_findings.txt"
TEST_PATIENT_DISEASES_OUTPUT_PATH = TEST_REPORT_DIR / "patient_diseases.txt"
TEST_PATIENT_EVENT_OUTPUT_PATH = TEST_REPORT_DIR / "patient_events.txt"
TEST_PATIENT_MEDICATION_OUTPUT_PATH = TEST_REPORT_DIR / "patient_medications.txt"
TEST_PATIENT_FINDINGS_OUTPUT_PATH = TEST_REPORT_DIR / "patient_findings.txt"
TEST_PATIENT_FINDING_LOCATIONS_OUTPUT_PATH = TEST_REPORT_DIR / "patient_finding_locations.txt"
TEST_PATIENT_FINDING_MORPHOLOGIES_OUTPUT_PATH = TEST_REPORT_DIR / "patient_finding_morphologies.txt"
TEST_PATIENT_LAB_SAMPLE_OUTPUT_PATH = TEST_REPORT_DIR / "patient_lab_samples.txt"
TEST_PATIENT_INTERVENTION_OUTPUT_PATH = TEST_REPORT_DIR / "patient_interventions.txt"
TEST_PATIENT_EXAMINATION_INDICATION_OUTPUT_PATH = TEST_REPORT_DIR / "patient_examination_indications.txt"

TEST_POLYP_MORPHOLOGY_CLASSIFICATION_NAMES = [
    "colon_lesion_size_mm",
    "colon_lesion_surface_intact_default",
    "colon_lesion_planarity_default",
    "colon_lesion_circularity_default",
    "colon_lesion_nice",
    "colon_lesion_paris",
]

COLONOSCOPY_FINDING_LOCATION_CLASSIFICATION_NAME = "colonoscopy_default"

TEST_ANTICOAGULATION_INDICATION_TYPES = [
    "thromboembolism-prevention-after_hip_replacement",
    "thromboembolism-prevention-after_knee_replacement",
    "thromboembolism-prevention-non_valvular_af",
    "thromboembolism-prevention-recurrent_thrombembolism",
    "deep_vein_thrombosis-treatment",
    "pulmonary_embolism-treatment",
    "thrombembolism-prevention-recurrent_thrombembolism"
]

TEST_CENTER_NAME = "test_center"


TEST_EXAMINATION_NAME_STRINGS = [
    "colonoscopy",
    "gastroscopy",
    "endosonography_upper_gi",
    "endosonography_lower_gi",
    "ercp",
    "small_bowel_endoscopy",
    "capsule_endoscopy"
]

LAB_VALUE_DICTS = [
    {
        "lab_value_name": "potassium",
        "value": 4.2,
    },
]

LAB_VALUE_W_DIST_DICTS = [
    {
        "lab_value_name": "sodium",
    }
]