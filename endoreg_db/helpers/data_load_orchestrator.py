"""Canonical data-load orchestration module.

This module provides the preferred import path for command-sequencing helpers.
Implementation remains in ``endoreg_db.helpers.data_loader`` for compatibility.
"""

from endoreg_db.helpers.data_loader import (
    load_ai_model_data,
    load_ai_model_label_data,
    load_base_db_data,
    load_center_data,
    load_contraindication_data,
    load_data,
    load_default_ai_model,
    load_disease_data,
    load_distribution_data,
    load_endoscope_data,
    load_event_data,
    load_examination_data,
    load_examination_indication_data,
    load_finding_data,
    load_gender_data,
    load_green_endoscopy_wuerzburg_data,
    load_information_source,
    load_lab_value_data,
    load_medication_data,
    load_organ_data,
    load_pdf_type_data,
    load_qualification_data,
    load_report_reader_flag_data,
    load_risk_data,
    load_shift_data,
    load_unit_data,
)


def load_all_reference_data():
    """Preferred explicit name for loading the full predefined reference set."""
    return load_data()


__all__ = [
    "load_default_ai_model",
    "load_qualification_data",
    "load_shift_data",
    "load_base_db_data",
    "load_information_source",
    "load_risk_data",
    "load_center_data",
    "load_endoscope_data",
    "load_distribution_data",
    "load_gender_data",
    "load_report_reader_flag_data",
    "load_pdf_type_data",
    "load_unit_data",
    "load_disease_data",
    "load_event_data",
    "load_organ_data",
    "load_contraindication_data",
    "load_examination_data",
    "load_lab_value_data",
    "load_finding_data",
    "load_examination_indication_data",
    "load_medication_data",
    "load_ai_model_label_data",
    "load_ai_model_data",
    "load_green_endoscopy_wuerzburg_data",
    "load_data",
    "load_all_reference_data",
]
