from pathlib import Path

TEST_CENTER_NAME = "Test Center"
TEST_CENTERS_AVAILABLE = [
    "university_hospital_wuerzburg",
    "rbk_stuttgart",
    "bbk_regensburg",
    "uke_hamburg",
    "rkh_ludwigsburg",
    "endoreg_db_demo"
]

TEST_REPORT_DIR = Path("test_reports")

TEST_CENTER_OUTPUT_PATH = TEST_REPORT_DIR / "center.txt"
TEST_ENDOSCOPE_OUTPUT_PATH = TEST_REPORT_DIR / "endoscope.txt"
TEST_GREEN_ENDOSCOPY_OUTPUT_PATH = TEST_REPORT_DIR / "green_endoscopy.txt"

