"""Create pydantic classes for ntx data."""

from pydantic import BaseModel


# Patient data model
class PatientData(BaseModel):
    patient_id_ntx: str
    patient_ids_ukw: list[str] = []
    case_ids_ukw: list[str] = []
    transplant_ids: list[str] = []
    patient_hash: str
    first_name: str
    last_name: str
    dob: str  # ISO format date string
    post_code: str
    city: str
    street: str


# Lab data model
class LabData(BaseModel):
    patient_id_ntx: str | None
    patient_id_ukw: str
    case_id_ukw: str
    test_name: str
    test_value: str
    test_datetime: str  # ISO format date string
    transplant_id: str | None = None


# Readout data model
class ReadoutData(BaseModel):
    transplant_id: str
    patient_id_ntx: str | None = None
    patient_id_ukw: str | None = None
    transplant_date: str | None = None  # ISO format date string
    first_name: str | None = None
    last_name: str | None = None
    dob: str | None = None  # ISO format date string
    patient_hash: str | None = None
    age_at_transplant: float | None = None
    transplant_number: int | None = None
    wait_time_esrd_to_transplant: float | None = None
    lsp_flag: bool | None = None
    aboi_flag: bool | None = None
    np_tx_flag: bool | None = None
    donor_age: int | None = None
    esp_flag: bool | None = None
    donor_gender: str | None = None
    donor_cause_of_death_category: str | None = None
    serum_creatinine_procurement: float | None = None
    cold_ischemia_time_hours: float | None = None
    delayed_graft_function: bool | None = None
    primary_disease: str | None = None
    primary_disease_category: str | None = None
    on_hemodialysis: bool | None = None
    on_peritoneal_dialysis: bool | None = None
    post_code: str | None = None
    distance_home_to_tx_center: float | None = None
    distance_category: str | None = None
    visits_tx_center_year1: float | None = None
    visits_tx_center_year2_plus: float | None = None
    follow_up_by: str | None = None
    graft_loss_date: str | None = None
    graft_loss_or_death_date: str | None = None
    date_of_death: str | None = None
    graft_loss: bool | None = None
    graft_survival_months: float | None = None
    is_deceased: bool | None = None
    patient_survival_months: float | None = None
    discontinued_graft_loss: bool | None = None
    discontinued_graft_survival_months: float | None = None
    graft_loss_cause: str | None = None
    cause_of_death: str | None = None
    maintenance_csa: bool | None = None
    maintenance_tac: bool | None = None
    maintenance_aza: bool | None = None
    maintenance_mmf: bool | None = None
    maintenance_rapamycin: bool | None = None
    maintenance_prednisone: bool | None = None
    acute_rejection_steroid_sensitive: bool | None = None
    acute_rejection_steroid_resistant: bool | None = None
    chronic_rejection: bool | None = None
    recurrent_urinary_tract_infections: bool | None = None
    cmv_infection: bool | None = None
    cmv_disease: bool | None = None
    malignancy: bool | None = None
    egfr_year_1: float | None = None
    egfr_year_3: float | None = None
    egfr_year_5: float | None = None
    egfr_year_10: float | None = None
    egfr_year_15: float | None = None
    egfr_year_20: float | None = None
    tpu_year_1: float | None = None
    tpu_year_3: float | None = None
    tpu_year_5: float | None = None
    tpu_year_10: float | None = None
    tpu_year_15: float | None = None
    tpu_year_20: float | None = None


# Transplant / registry data model
class TransplantData(BaseModel):
    # identifiers
    transplant_id: str | None = None
    patient_id_ntx: str | None = None
    patient_id_ukw: str | None = None

    # demographics
    first_name: str | None = None
    last_name: str | None = None
    dob: str | None = None
    post_code: str | None = None
    city: str | None = None
    street: str | None = None

    # transplant context
    transplant_date: str | None = None
    age_at_transplant: float | None = None
    transplant_number: int | None = None
    wait_time_esrd_to_transplant: float | None = None
    total_transplant_duration: float | None = None
    lsp_flag: bool | None = None
    aboi_flag: bool | None = None
    np_tx_flag: bool | None = None
    living_donor: bool | None = None
    esp_flag: bool | None = None
    donor_age: int | None = None
    donor_gender: str | None = None
    donor_cause_of_death_category: str | None = None
    serum_creatinine_procurement: float | None = None
    cold_ischemia_time_hours: float | None = None
    delayed_graft_function: bool | None = None

    # follow-up / outcomes
    follow_up_by: str | None = None
    graft_loss: bool | None = None
    graft_loss_date: str | None = None
    graft_loss_or_death_date: str | None = None
    graft_loss_cause: str | None = None
    graft_survival_months: float | None = None
    discontinued_graft_loss: bool | None = None
    discontinued_graft_survival_months: float | None = None
    is_deceased: bool | None = None
    date_of_death: str | None = None
    patient_survival_months: float | None = None
    cause_of_death: str | None = None
    dialysis_restart_date: str | None = None
    lost_to_follow_up: bool | None = None

    # disease & comorbidities
    primary_disease: str | None = None
    primary_disease_category: str | None = None
    diabetes: bool | None = None
    ptdm: bool | None = None
    malignancy: bool | None = None

    # dialysis & follow-up logistics
    on_hemodialysis: bool | None = None
    on_peritoneal_dialysis: bool | None = None
    dialysis_center: str | None = None
    distance_home_to_tx_center: float | None = None
    distance_category: str | None = None
    visits_tx_center_year1: float | None = None
    visits_tx_center_year2_plus: float | None = None

    # anthropometrics
    height_cm: float | None = None
    weight_kg: float | None = None

    # infections / serology
    cmv_igg_donor: str | None = None
    cmv_igg_recipient: str | None = None
    cmv_infection: bool | None = None
    cmv_disease: bool | None = None
    recurrent_urinary_tract_infections: bool | None = None

    # immunosuppression
    okt_induction: bool | None = None
    atg_induction: bool | None = None
    simulect_induction: bool | None = None
    on_csa: bool | None = None
    on_tac: bool | None = None
    on_aza: bool | None = None
    on_mmf: bool | None = None
    on_steroids: bool | None = None
    maintenance_csa: bool | None = None
    maintenance_tac: bool | None = None
    maintenance_aza: bool | None = None
    maintenance_mmf: bool | None = None
    maintenance_rapamycin: bool | None = None
    maintenance_prednisone: bool | None = None
    acute_rejection_steroid_sensitive: bool | None = None
    acute_rejection_steroid_resistant: bool | None = None
    chronic_rejection: bool | None = None

    # longitudinal kidney function
    egfr_year_1: float | None = None
    egfr_year_3: float | None = None
    egfr_year_5: float | None = None
    egfr_year_10: float | None = None
    egfr_year_15: float | None = None
    egfr_year_20: float | None = None
    tpu_year_1: float | None = None
    tpu_year_3: float | None = None
    tpu_year_5: float | None = None
    tpu_year_10: float | None = None
    tpu_year_15: float | None = None
    tpu_year_20: float | None = None

    # derived identifiers
    patient_hash: str | None = None
