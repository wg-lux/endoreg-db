from pathlib import Path
import warnings

TEST_GASTRO_VIDEO_PATH = Path("tests/sensitive/lux-gastro-video.mp4")
TEST_GASTRO_REPORT_PATH = Path("tests/sensitive/lux-gastro-report.pdf")
TEST_GASTRO_HISTO_PATH = Path("tests/sensitive/lux-gastro-histo.pdf")
TEST_SENSITIVE_OUTPUT_DIR = Path("tests/sensitive/results")
TEST_GASTRO_REPORT_OUTPUT_PATH = TEST_SENSITIVE_OUTPUT_DIR / "lux-gastro-report.txt"

TEST_CENTER_NAME = "university_hospital_wuerzburg"

TEST_FRAME_DIR = Path("sensitive/frames")
if not TEST_FRAME_DIR.exists():
    TEST_FRAME_DIR.mkdir(parents=True)

if not TEST_SENSITIVE_OUTPUT_DIR.exists():
    TEST_SENSITIVE_OUTPUT_DIR.mkdir(parents=True)

def check_file_exists(file_path:Path):
    assert isinstance(file_path, Path), "file_path must be a Path object"
    if not file_path.exists():
        warnings.warn(f"File {file_path} does not exist")
        return False
    
    else:
        return True