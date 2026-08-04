"""Create pydantic classes for ntx data."""
from pydantic import BaseModel
from typing import List, Optional


# Patient data model
class PatientData(BaseModel):
    patient_id_ntx: str
    patient_ids_ukw: List[str] = []
    case_ids_ukw: List[str] = []
    transplant_ids: List[str] = []
    patient_hash: str
    first_name: str
    last_name: str
    dob: str  # ISO format date string
    post_code: str
    city: str
    street: str


# Lab data model
class LabData(BaseModel):
    patient_id_ntx: Optional[str]
    patient_id_ukw: str
    case_id_ukw: str
    test_name: str
    test_value: str
    test_datetime: str  # ISO format date string
    transplant_id: Optional[str] = None


# Readout data model
class ReadoutData(BaseModel):
    transplant_id: str
    patient_id_ntx: Optional[str] = None
    patient_id_ukw: Optional[str] = None
    transplant_date: Optional[str] = None  # ISO format date string
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None  # ISO format date string
    patient_hash: Optional[str] = None
    age_at_transplant: Optional[float] = None
    transplant_number: Optional[int] = None
    wait_time_esrd_to_transplant: Optional[float] = None
    lsp_flag: Optional[bool] = None
    aboi_flag: Optional[bool] = None
    np_tx_flag: Optional[bool] = None
    donor_age: Optional[int] = None
    esp_flag: Optional[bool] = None
    donor_gender: Optional[str] = None
    donor_cause_of_death_category: Optional[str] = None
    serum_creatinine_procurement: Optional[float] = None
    cold_ischemia_time_hours: Optional[float] = None
    delayed_graft_function: Optional[bool] = None
    primary_disease: Optional[str] = None
    primary_disease_category: Optional[str] = None
    on_hemodialysis: Optional[bool] = None
    on_peritoneal_dialysis: Optional[bool] = None
    post_code: Optional[str] = None
    distance_home_to_tx_center: Optional[float] = None
    distance_category: Optional[str] = None
    visits_tx_center_year1: Optional[float] = None
    visits_tx_center_year2_plus: Optional[float] = None
    follow_up_by: Optional[str] = None
    graft_loss_date: Optional[str] = None
    graft_loss_or_death_date: Optional[str] = None
    date_of_death: Optional[str] = None
    graft_loss: Optional[bool] = None
    graft_survival_months: Optional[float] = None
    is_deceased: Optional[bool] = None
    patient_survival_months: Optional[float] = None
    discontinued_graft_loss: Optional[bool] = None
    discontinued_graft_survival_months: Optional[float] = None
    graft_loss_cause: Optional[str] = None
    cause_of_death: Optional[str] = None
    maintenance_csa: Optional[bool] = None
    maintenance_tac: Optional[bool] = None
    maintenance_aza: Optional[bool] = None
    maintenance_mmf: Optional[bool] = None
    maintenance_rapamycin: Optional[bool] = None
    maintenance_prednisone: Optional[bool] = None
    acute_rejection_steroid_sensitive: Optional[bool] = None
    acute_rejection_steroid_resistant: Optional[bool] = None
    chronic_rejection: Optional[bool] = None
    recurrent_urinary_tract_infections: Optional[bool] = None
    cmv_infection: Optional[bool] = None
    cmv_disease: Optional[bool] = None
    malignancy: Optional[bool] = None
    egfr_year_1: Optional[float] = None
    egfr_year_3: Optional[float] = None
    egfr_year_5: Optional[float] = None
    egfr_year_10: Optional[float] = None
    egfr_year_15: Optional[float] = None
    egfr_year_20: Optional[float] = None
    tpu_year_1: Optional[float] = None
    tpu_year_3: Optional[float] = None
    tpu_year_5: Optional[float] = None
    tpu_year_10: Optional[float] = None
    tpu_year_15: Optional[float] = None
    tpu_year_20: Optional[float] = None


# Transplant / registry data model
class TransplantData(BaseModel):
    # identifiers
    transplant_id: Optional[str] = None
    patient_id_ntx: Optional[str] = None
    patient_id_ukw: Optional[str] = None

    # demographics
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None
    post_code: Optional[str] = None
    city: Optional[str] = None
    street: Optional[str] = None

    # transplant context
    transplant_date: Optional[str] = None
    age_at_transplant: Optional[float] = None
    transplant_number: Optional[int] = None
    wait_time_esrd_to_transplant: Optional[float] = None
    total_transplant_duration: Optional[float] = None
    lsp_flag: Optional[bool] = None
    aboi_flag: Optional[bool] = None
    np_tx_flag: Optional[bool] = None
    living_donor: Optional[bool] = None
    esp_flag: Optional[bool] = None
    donor_age: Optional[int] = None
    donor_gender: Optional[str] = None
    donor_cause_of_death_category: Optional[str] = None
    serum_creatinine_procurement: Optional[float] = None
    cold_ischemia_time_hours: Optional[float] = None
    delayed_graft_function: Optional[bool] = None

    # follow-up / outcomes
    follow_up_by: Optional[str] = None
    graft_loss: Optional[bool] = None
    graft_loss_date: Optional[str] = None
    graft_loss_or_death_date: Optional[str] = None
    graft_loss_cause: Optional[str] = None
    graft_survival_months: Optional[float] = None
    discontinued_graft_loss: Optional[bool] = None
    discontinued_graft_survival_months: Optional[float] = None
    is_deceased: Optional[bool] = None
    date_of_death: Optional[str] = None
    patient_survival_months: Optional[float] = None
    cause_of_death: Optional[str] = None
    dialysis_restart_date: Optional[str] = None
    lost_to_follow_up: Optional[bool] = None

    # disease & comorbidities
    primary_disease: Optional[str] = None
    primary_disease_category: Optional[str] = None
    diabetes: Optional[bool] = None
    ptdm: Optional[bool] = None
    malignancy: Optional[bool] = None

    # dialysis & follow-up logistics
    on_hemodialysis: Optional[bool] = None
    on_peritoneal_dialysis: Optional[bool] = None
    dialysis_center: Optional[str] = None
    distance_home_to_tx_center: Optional[float] = None
    distance_category: Optional[str] = None
    visits_tx_center_year1: Optional[float] = None
    visits_tx_center_year2_plus: Optional[float] = None

    # anthropometrics
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None

    # infections / serology
    cmv_igg_donor: Optional[str] = None
    cmv_igg_recipient: Optional[str] = None
    cmv_infection: Optional[bool] = None
    cmv_disease: Optional[bool] = None
    recurrent_urinary_tract_infections: Optional[bool] = None

    # immunosuppression
    okt_induction: Optional[bool] = None
    atg_induction: Optional[bool] = None
    simulect_induction: Optional[bool] = None
    on_csa: Optional[bool] = None
    on_tac: Optional[bool] = None
    on_aza: Optional[bool] = None
    on_mmf: Optional[bool] = None
    on_steroids: Optional[bool] = None
    maintenance_csa: Optional[bool] = None
    maintenance_tac: Optional[bool] = None
    maintenance_aza: Optional[bool] = None
    maintenance_mmf: Optional[bool] = None
    maintenance_rapamycin: Optional[bool] = None
    maintenance_prednisone: Optional[bool] = None
    acute_rejection_steroid_sensitive: Optional[bool] = None
    acute_rejection_steroid_resistant: Optional[bool] = None
    chronic_rejection: Optional[bool] = None

    # longitudinal kidney function
    egfr_year_1: Optional[float] = None
    egfr_year_3: Optional[float] = None
    egfr_year_5: Optional[float] = None
    egfr_year_10: Optional[float] = None
    egfr_year_15: Optional[float] = None
    egfr_year_20: Optional[float] = None
    tpu_year_1: Optional[float] = None
    tpu_year_3: Optional[float] = None
    tpu_year_5: Optional[float] = None
    tpu_year_10: Optional[float] = None
    tpu_year_15: Optional[float] = None
    tpu_year_20: Optional[float] = None

    # derived identifiers
    patient_hash: Optional[str] = None
