from pathlib import Path
import warnings

TEST_SALT = "test_salt"

TEST_GASTRO_VIDEO_PATH = Path("tests/sensitive/lux-gastro-video.mp4")
TEST_GASTRO_REPORT_PATH = Path("tests/sensitive/lux-gastro-report.pdf")
TEST_GASTRO_REPORT_RESULTS = {
    "patient_hash": "4dfab952b30cda0a8a1b4abd6f460d2de5c46f7f72a80d438d63632ee7c614b4",
    "patient_examination_hash": "04ffe57d2a1f6fab17f17c71f4b25e5e4247d4d949ba07dce8a98306b78dcd3d"
        
}
TEST_GASTRO_HISTO_PATH = Path("tests/sensitive/lux-gastro-histo.pdf")
TEST_SENSITIVE_OUTPUT_DIR = Path("tests/sensitive/results")
TEST_GASTRO_REPORT_OUTPUT_PATH = TEST_SENSITIVE_OUTPUT_DIR / "lux-gastro-report.txt"
TEST_GASTROSCOPY_VIDEO_OUTPUT_PATH = TEST_SENSITIVE_OUTPUT_DIR / "lux-gastro-video.txt"

TEST_MULTILABEL_AI_MODEL_IMPORT_PATH = Path("tests/sensitive/multilabel_ai_model.txt")
TEST_MULTILABEL_CLASSIFIER_MODEL_PATH = Path("tests/sensitive/models/colo_segmentation_RegNetX800MF_6.ckpt")

TEST_CENTER_NAME = "university_hospital_wuerzburg"

VIDEO_SOURCE_ROOT = Path("tests/sensitive/raw_videos")


TEST_VIDEO_DICT = {
    "gastroscopy": {
        "path": Path("tests/sensitive/raw_videos/003.mp4"),
        "examination_types": [
            "gastroscopy",
        ],
        "skip": True,
        "endoscopy_processor": "olympus_cv_1500",
        "results": {
            "patient_hash": "4dfab952b30cda0a8a1b4abd6f460d2de5c46f7f72a80d438d63632ee7c614b4",
            "patient_examination_hash": "04ffe57d2a1f6fab17f17c71f4b25e5e4247d4d949ba07dce8a98306b78dcd3d"
        }
    }
}


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