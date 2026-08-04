# endoreg_db/import_files/processing/sensitive_meta_adapter.py
from typing import Any, Dict

from lx_anonymizer.sensitive_meta_interface import SensitiveMeta as LxSensitiveMeta


def normalize_lx_sensitive_meta(meta: LxSensitiveMeta) -> Dict[str, Any]:
    """
    Convert lx_anonymizer.SensitiveMeta into a dict suitable for
    endoreg_db SensitiveMeta.update_from_dict / create_from_dict.

    - Renames fields where necessary (center -> center_name, patient_gender_name -> patient_gender)
    - Drops None/blank values (your update logic already handles blanks carefully)
    - Leaves dates as strings; your logic layer already parses them
    """
    raw = meta.to_dict()
    out: Dict[str, Any] = {}

    # 1:1 fields (same names in model logic)
    direct_keys = [
        "file_path",
        "patient_first_name",
        "patient_last_name",
        "patient_dob",  # string; logic has parsing
        "casenumber",
        "examination_date",  # string; logic has parsing
        "examination_time",  # string "HH:MM" is fine
        "examiner_first_name",
        "examiner_last_name",
        "text",
        "anonymized_text",
        "endoscope_type",
        "endoscope_sn",
    ]
    for k in direct_keys:
        v = raw.get(k)
        if v not in (None, "", []):
            out[k] = v

    # Map patient_gender_name (interface) -> patient_gender (logic)
    gender_name = raw.get("patient_gender_name")
    if gender_name not in (None, ""):
        # Your logic.update_* can handle strings for patient_gender
        out["patient_gender"] = gender_name

    # Map center (string) -> center_name (logic)
    center_name = raw.get("center")
    if center_name not in (None, ""):
        out["center_name"] = center_name

    return out
