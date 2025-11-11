from typing import Any, Dict, List, Optional

import pandas as pd

from .datamodels import PatientData, TransplantData
from . import (
    compute_patient_hash,
    fu_tx_distance_df_path,
    load_dataframe,
    rename_transplant_df_dict,
)

TRUE_VALUES = {"1", "true", "yes", "y", "ja", "j", "wahr"}
FALSE_VALUES = {"0", "false", "no", "n", "nein"}

DATE_FIELDS = {
    "dob",
    "transplant_date",
    "graft_loss_date",
    "graft_loss_or_death_date",
    "date_of_death",
    "dialysis_restart_date",
}

INT_FIELDS = {"patient_id_ntx", "transplant_number", "donor_age"}

FLOAT_FIELDS = {
    "age_at_transplant",
    "wait_time_esrd_to_transplant",
    "total_transplant_duration",
    "serum_creatinine_procurement",
    "cold_ischemia_time_hours",
    "graft_survival_months",
    "discontinued_graft_survival_months",
    "patient_survival_months",
    "distance_home_to_tx_center",
    "visits_tx_center_year1",
    "visits_tx_center_year2_plus",
    "height_cm",
    "weight_kg",
    "egfr_year_1",
    "egfr_year_3",
    "egfr_year_5",
    "egfr_year_10",
    "egfr_year_15",
    "egfr_year_20",
    "tpu_year_1",
    "tpu_year_3",
    "tpu_year_5",
    "tpu_year_10",
    "tpu_year_15",
    "tpu_year_20",
}

BOOL_FIELDS = {
    "lsp_flag",
    "aboi_flag",
    "np_tx_flag",
    "living_donor",
    "esp_flag",
    "delayed_graft_function",
    "graft_loss",
    "discontinued_graft_loss",
    "is_deceased",
    "lost_to_follow_up",
    "diabetes",
    "ptdm",
    "malignancy",
    "on_hemodialysis",
    "on_peritoneal_dialysis",
    "cmv_infection",
    "cmv_disease",
    "recurrent_urinary_tract_infections",
    "okt_induction",
    "atg_induction",
    "simulect_induction",
    "on_csa",
    "on_tac",
    "on_aza",
    "on_mmf",
    "on_steroids",
    "maintenance_csa",
    "maintenance_tac",
    "maintenance_aza",
    "maintenance_mmf",
    "maintenance_rapamycin",
    "maintenance_prednisone",
    "acute_rejection_steroid_sensitive",
    "acute_rejection_steroid_resistant",
    "chronic_rejection",
}

STRING_FIELDS = {
    "transplant_id",
    "patient_id_ukw",
    "first_name",
    "last_name",
    "post_code",
    "city",
    "street",
    "follow_up_by",
    "graft_loss_cause",
    "cause_of_death",
    "primary_disease",
    "primary_disease_category",
    "donor_gender",
    "donor_cause_of_death_category",
    "distance_category",
    "dialysis_center",
    "cmv_igg_donor",
    "cmv_igg_recipient",
}


def _normalize_bool(value: Any) -> Optional[bool]:
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return None
        if normalized in TRUE_VALUES:
            return True
        if normalized in FALSE_VALUES:
            return False
    return None


def _normalize_int(value: Any) -> Optional[int]:
    if pd.isna(value) or value == "":
        return None
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        cleaned = cleaned.replace(",", ".")
        try:
            return int(float(cleaned))
        except ValueError:
            return None
    return None


def _normalize_float(value: Any) -> Optional[float]:
    if pd.isna(value) or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _normalize_string(value: Any) -> Optional[str]:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _normalize_date(value: Any) -> Optional[str]:
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return str(value)


def _normalize_transplant_value(column: str, value: Any) -> Any:
    if column in DATE_FIELDS:
        return _normalize_date(value)
    if column in BOOL_FIELDS:
        return _normalize_bool(value)
    if column in INT_FIELDS:
        return _normalize_int(value)
    if column in FLOAT_FIELDS:
        return _normalize_float(value)
    if column in STRING_FIELDS:
        return _normalize_string(value)
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return value


def serialize_fu_tx_distance_df(df: pd.DataFrame) -> List[TransplantData]:
    fu_tx_distance_data_list: List[TransplantData] = []
    for _, row in df.iterrows():
        record: Dict[str, Any] = {}
        for column, value in row.items():
            column_name = str(column)
            record[column_name] = _normalize_transplant_value(column_name, value)
        record["patient_hash"] = compute_patient_hash(row)
        record["patient_id_ntx"] = str(record.get("patient_id_ntx", ""))
        fu_tx_distance_data_list.append(TransplantData(**record))

    return fu_tx_distance_data_list


def fu_tx_distance_df_etl(
    patient_data_list: List[PatientData],
) -> List[TransplantData]:
    lookup_patient_id_ntx_for_transplant_id: Dict[str, Optional[str]] = {}
    for patient_data in patient_data_list:
        for transplant_id in patient_data.transplant_ids:
            if transplant_id:
                lookup_patient_id_ntx_for_transplant_id[str(transplant_id).strip()] = (
                    patient_data.patient_id_ntx
                )

    fu_tx_distance_df = load_dataframe(
        fu_tx_distance_df_path, rename_dict=rename_transplant_df_dict
    )

    def _lookup_patient_id(tx_id: Any) -> Optional[str]:
        if pd.isna(tx_id):
            return None
        if isinstance(tx_id, float) and tx_id.is_integer():
            key = str(int(tx_id))
        else:
            key = str(tx_id).strip()
        return lookup_patient_id_ntx_for_transplant_id.get(key)

    fu_tx_distance_df["patient_id_ntx"] = fu_tx_distance_df["transplant_id"].apply(
        _lookup_patient_id
    )

    fu_tx_distance_data_list: List[TransplantData] = serialize_fu_tx_distance_df(
        fu_tx_distance_df
    )

    return fu_tx_distance_data_list