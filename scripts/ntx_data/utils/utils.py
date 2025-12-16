from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from endoreg_db.utils.hashs import get_patient_hash

from .datamodels import LabData, PatientData, ReadoutData

###################################################
raw_data_dir = Path("./data/ntx-data/raw")
processed_data_dir = Path("./data/ntx-data/clean")

patient_df_path = raw_data_dir / "Match mit ID.xlsx"
rename_patient_df_dict = {
    "Nachname": "last_name",
    "Vorname": "first_name",
    "Geb": "dob",
    "PatientNr": "patient_id_ukw",
    "ID": "patient_id_ntx",
    "PLZ": "post_code",
    "Wohnort": "city",
    "Strasse": "street",
}

readout_df_path = raw_data_dir / "readout.xlsx"
rename_readout_df_dict = {
    "Lfd Nr. ": "transplant_id",
    "StudienID": "patient_id_ntx",
    "Nachname": "last_name",
    "Vorname": "first_name",
    "DoB": "dob",
    "Tx am": "transplant_date",
    "Alter bei Tx": "age_at_transplant",
    "Zahl Tx": "transplant_number",
    "Warte-zeit ESRD => Tx": "wait_time_esrd_to_transplant",
    "LSP ?": "lsp_flag",
    "ABOi?": "aboi_flag",
    "NPTx ?": "np_tx_flag",
    "DON Alter": "donor_age",
    "ESP ?": "esp_flag",
    "Spender-geschlecht": "donor_gender",
    "Kategorie Todesur-sache Don": "donor_cause_of_death_category",
    "S-Krea bei Entnahme": "serum_creatinine_procurement",
    "CIT in h": "cold_ischemia_time_hours",
    "DGF": "delayed_graft_function",
    "Grunderkrankung": "primary_disease",
    "Kategorie GE": "primary_disease_category",
    "HD?": "on_hemodialysis",
    "PD?": "on_peritoneal_dialysis",
    "PLZ": "post_code",
    "Entfernung Wohnort -TxZ (97080)": "distance_home_to_tx_center",
    "Kategorie Entfernung": "distance_category",
    "Kontakte TxZ Jahr 1 post Tx": "visits_tx_center_year1",
    "Kontakte TxZ Jahr 2 ff / Jahr": "visits_tx_center_year2_plus",
    "Nachbetreuung durch": "follow_up_by",
    "TxVerlust am:": "graft_loss_date",
    "TxVerlust oder Tod am:": "graft_loss_or_death_date",
    "Verstorben am:": "date_of_death",
    "Graft loss": "graft_loss",
    "graft Surv in Monaten": "graft_survival_months",
    "Tod": "is_deceased",
    "PatSurv in Monaten": "patient_survival_months",
    "dc graft loss": "discontinued_graft_loss",
    "dc graft surv in Monaten": "discontinued_graft_survival_months",
    "Ursache Transplantatverlust": "graft_loss_cause",
    "Todesursache": "cause_of_death",
    "Erhaltung CsA": "maintenance_csa",
    "Erhaltung Tac": "maintenance_tac",
    "Erhaltung AZA": "maintenance_aza",
    "Erhaltung MMF": "maintenance_mmf",
    "Erhaltung Rapa": "maintenance_rapamycin",
    "Erhaltung Pred": "maintenance_prednisone",
    "AcRej, steroidsensibel": "acute_rejection_steroid_sensitive",
    "AcRej, nicht steroidsensibel": "acute_rejection_steroid_resistant",
    "Chron. Rej": "chronic_rejection",
    "Rezidiv HWI?": "recurrent_urinary_tract_infections",
    "CMV-Infekt": "cmv_infection",
    "CMV-Disease": "cmv_disease",
    "Malign": "malignancy",
    "eGFR Jahr 1": "egfr_year_1",
    "eGFR Jahr 3": "egfr_year_3",
    "eGFR Jahr 5": "egfr_year_5",
    "eGFR Jahr 10": "egfr_year_10",
    "eGFR Jahr 15": "egfr_year_15",
    "eGFR Jahr 20": "egfr_year_20",
    "TPU Jahr 1": "tpu_year_1",
    "TPU Jahr 3": "tpu_year_3",
    "TPU Jahr 5": "tpu_year_5",
    "TPU Jahr 10": "tpu_year_10",
    "TPU Jahr 15": "tpu_year_15",
    "TPU Jahr 20": "tpu_year_20",
}

fu_tx_distance_df_path = raw_data_dir / "fu_tx-distance.xlsx"
rename_transplant_df_dict = {
    "Lfd Nummer": "transplant_id",
    "Tx am": "transplant_date",
    "Nachname": "last_name",
    "Vorname": "first_name",
    "Geburtsdatum": "dob",
    "Nachbetreuung durch": "follow_up_by",
    "Verstorben:": "is_deceased",
    "Transplantatverlust": "graft_loss",
    "Ursache Transplantatverlust": "graft_loss_cause",
    "Lebendspende ?": "living_donor",
    "ESP ?": "esp_flag",
    "Verstorben am:": "date_of_death",
    "Todesursache": "cause_of_death",
    "PLZ": "post_code",
    "Stadt": "city",
    "Grunderkrankung": "primary_disease",
    "HD?": "on_hemodialysis",
    "PD?": "on_peritoneal_dialysis",
    "Diabetes mellitus": "diabetes",
    "Wievielte Tx?": "transplant_number",
    "Wartezeit ESRD => Tx": "wait_time_esrd_to_transplant",
    "Koerpergroeße": "height_cm",
    "Gewicht": "weight_kg",
    "Spenderalter": "donor_age",
    "CMV-IgG D": "cmv_igg_donor",
    "CMV-IgG R": "cmv_igg_recipient",
    "OKT-Induktion": "okt_induction",
    "ATG-Induktion": "atg_induction",
    "Simulect-Induktion": "simulect_induction",
    "CSA": "on_csa",
    "TAC (FK506)": "on_tac",
    "AZA": "on_aza",
    "MMF": "on_mmf",
    "Steroide": "on_steroids",
    "Erhaltung CsA": "maintenance_csa",
    "Erhaltung Tac": "maintenance_tac",
    "Erhaltung AZA": "maintenance_aza",
    "Erhaltung MMF": "maintenance_mmf",
    "Erhaltung Rapa": "maintenance_rapamycin",
    "Erhaltung Pred": "maintenance_prednisone",
    "AcRej, steroidsensibel": "acute_rejection_steroid_sensitive",
    "AcRej, nicht steroidsensibel": "acute_rejection_steroid_resistant",
    "Chronische Rejektion": "chronic_rejection",
    "PTDM ?": "ptdm",
    "CMV-Infekt": "cmv_infection",
    "CMV-Disease": "cmv_disease",
    "Rezidiv HW-Infekte?": "recurrent_urinary_tract_infections",
    "Lost to follow up:": "lost_to_follow_up",
    "Erneute Dialyse seit:": "dialysis_restart_date",
    "Dialysezentrum:": "dialysis_center",
    "Malignom": "malignancy",
    "Gesamt-Tx-Laufzeit:": "total_transplant_duration",
}

lab_df_path = raw_data_dir / "Labore.xlsx"
rename_lab_df_dict = {
    "PatientNr": "patient_id_ukw",
    "fallnr": "case_id_ukw",
    "Leistungstext": "test_name",
    "messwert": "test_value",
    "Erbringungszeit": "test_datetime",
}

###################################################
LAB_DF_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def load_dataframe(df_path: Path, rename_dict: Dict[str, str]):
    assert df_path.exists(), f"Dataframe path {df_path} does not exist."
    df = pd.read_excel(df_path)
    df = df.rename(columns=rename_dict)

    # if first_name or last_name columns exist, replace special characters
    if "first_name" in df.columns:
        df["first_name"] = df["first_name"].apply(replace_special_characters)
    if "last_name" in df.columns:
        df["last_name"] = df["last_name"].apply(replace_special_characters)

    if "dob" in df.columns:
        # expected format is <YYYY-MM-DD>
        df["dob"] = pd.to_datetime(df["dob"], errors="coerce", dayfirst=True, format="%Y-%m-%d")

    if "test_datetime" in df.columns:
        # expected format is <YYYY-MM-DD HH:MM:SS.sss>
        # Example: 2014-11-27 12:08:00.000
        df["test_datetime"] = pd.to_datetime(df["test_datetime"], errors="coerce", dayfirst=True, format=LAB_DF_DATETIME_FORMAT)

    for date_column in (
        "transplant_date",
        "date_of_death",
        "graft_loss_date",
        "graft_loss_or_death_date",
        "dialysis_restart_date",
    ):
        if date_column in df.columns:
            df[date_column] = pd.to_datetime(df[date_column], errors="coerce", dayfirst=True)

    if "first_name" in df.columns and "last_name" in df.columns and "dob" in df.columns:
        # apply function to each row
        df["patient_hash"] = df.apply(compute_patient_hash, axis=1)

    return df


def replace_special_characters(text):
    if not text:
        text = ""
    text = str(text).lower()
    text = text.replace("ä", "ae")
    text = text.replace("ö", "oe")
    text = text.replace("ü", "ue")
    text = text.replace("ß", "ss")
    return text


def compute_patient_hash(row):
    first_name = row["first_name"]
    last_name = row["last_name"]
    dob = row["dob"]  # fixed salt for ntx data

    patient_hash = get_patient_hash(first_name=first_name, last_name=last_name, dob=dob, center="ntx-ukw")
    return patient_hash


from icecream import ic


def serialize_patient_df(df) -> List[PatientData]:
    data_list = []
    patients_dict_preparation = {}
    ic(f"Serializing {len(df)} patient records.")

    n_unique_patient_id_ntx = df["patient_id_ntx"].nunique()
    ic(f"Found {n_unique_patient_id_ntx} unique patient_id_ntx values.")
    for _, row in df.iterrows():
        patient_id_ntx = row["patient_id_ntx"]
        if pd.isna(patient_id_ntx):
            print("Warning: Skipping row with missing patient_id_ntx")
            print(row)
            continue
        if patient_id_ntx not in patients_dict_preparation:
            post_code = row["post_code"]
            if pd.notnull(post_code):
                # make sure to map int, float, strings to str
                # all entries must be strings consisting of digits only and have exactly 5 characters
                if isinstance(post_code, float) and post_code.is_integer():
                    post_code = str(int(post_code))
                elif not isinstance(post_code, str):
                    post_code = str(post_code)
                post_code = post_code.zfill(5)  # pad with leading zeros if necessary
                post_code = post_code.replace('"', "")

            else:
                post_code = "NA"

            patients_dict_preparation[patient_id_ntx] = {
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "dob": row["dob"].strftime("%Y-%m-%d") if pd.notnull(row["dob"]) else None,
                "post_code": post_code,
                "city": str(row["city"]) if pd.notnull(row["city"]) else "",
                "street": str(row["street"]) if pd.notnull(row["street"]) else "",
                "patient_id_ukw_list": [],
                "patient_hash": row["patient_hash"],
            }
        patients_dict_preparation[patient_id_ntx]["patient_id_ukw_list"].append(str(row["patient_id_ukw"]))

    for patient_id_ntx, data in patients_dict_preparation.items():
        patient_data = PatientData(
            patient_id_ntx=str(patient_id_ntx),
            patient_ids_ukw=data["patient_id_ukw_list"],
            patient_hash=data["patient_hash"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            dob=data["dob"],
            post_code=data["post_code"],
            city=data["city"],
            street=data["street"],
        )
        data_list.append(patient_data)

    assert len(data_list) == n_unique_patient_id_ntx, f"Expected {n_unique_patient_id_ntx} unique PatientData entries, but got {len(data_list)}."
    return data_list


def serialize_readout_df(df: pd.DataFrame) -> List[ReadoutData]:
    readout_data_list: List[ReadoutData] = []

    def _sanitize(value):
        if value is None:
            return None
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed or trimmed.lower() == "nan":
                return None
            return trimmed
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if pd.isna(value):
            return None
        return value

    def _sanitize_post_code(value):
        sanitized = _sanitize(value)
        if sanitized is None:
            return None
        post_code_str = str(sanitized).replace('"', "").strip()
        if not post_code_str:
            return None
        if post_code_str.isdigit() and len(post_code_str) <= 5:
            post_code_str = post_code_str.zfill(5)
        return post_code_str

    def _stringify_identifier(value):
        sanitized = _sanitize(value)
        if sanitized is None:
            return None
        if isinstance(sanitized, float) and sanitized.is_integer():
            return str(int(sanitized))
        return str(sanitized)

    def _sanitize_numeric(value, *, as_int: bool = False):
        sanitized = _sanitize(value)
        if sanitized is None:
            return None
        if isinstance(sanitized, (int, float)):
            number = float(sanitized)
        elif isinstance(sanitized, str):
            normalized = sanitized.replace(",", ".")
            if not normalized:
                return None
            try:
                number = float(normalized)
            except ValueError:
                return None
        else:
            return None
        if as_int:
            return int(number)
        return number

    def _sanitize_bool(value):
        sanitized = _sanitize(value)
        if sanitized is None:
            return None
        if isinstance(sanitized, bool):
            return sanitized
        if isinstance(sanitized, (int, float)):
            return sanitized != 0
        if isinstance(sanitized, str):
            normalized = sanitized.lower()
            if normalized in {"true", "t", "yes", "y", "1"}:
                return True
            if normalized in {"false", "f", "no", "n", "0"}:
                return False
            normalized = normalized.replace(",", ".")
            try:
                return float(normalized) != 0.0
            except ValueError:
                return None
        return None

    def _sanitize_text(value):
        sanitized = _sanitize(value)
        if sanitized is None:
            return None
        return str(sanitized).strip()

    optional_fields = [
        "age_at_transplant",
        "transplant_number",
        "wait_time_esrd_to_transplant",
        "lsp_flag",
        "aboi_flag",
        "np_tx_flag",
        "donor_age",
        "esp_flag",
        "donor_gender",
        "donor_cause_of_death_category",
        "serum_creatinine_procurement",
        "cold_ischemia_time_hours",
        "delayed_graft_function",
        "primary_disease",
        "primary_disease_category",
        "on_hemodialysis",
        "on_peritoneal_dialysis",
        "post_code",
        "distance_home_to_tx_center",
        "distance_category",
        "visits_tx_center_year1",
        "visits_tx_center_year2_plus",
        "follow_up_by",
        "graft_loss_date",
        "graft_loss_or_death_date",
        "date_of_death",
        "graft_loss",
        "graft_survival_months",
        "is_deceased",
        "patient_survival_months",
        "discontinued_graft_loss",
        "discontinued_graft_survival_months",
        "graft_loss_cause",
        "cause_of_death",
        "maintenance_csa",
        "maintenance_tac",
        "maintenance_aza",
        "maintenance_mmf",
        "maintenance_rapamycin",
        "maintenance_prednisone",
        "acute_rejection_steroid_sensitive",
        "acute_rejection_steroid_resistant",
        "chronic_rejection",
        "recurrent_urinary_tract_infections",
        "cmv_infection",
        "cmv_disease",
        "malignancy",
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
    ]

    egfr_fields = {f"egfr_year_{suffix}" for suffix in ("1", "3", "5", "10", "15", "20")}
    tpu_fields = {f"tpu_year_{suffix}" for suffix in ("1", "3", "5", "10", "15", "20")}

    float_fields = {
        "age_at_transplant",
        "wait_time_esrd_to_transplant",
        "serum_creatinine_procurement",
        "cold_ischemia_time_hours",
        "distance_home_to_tx_center",
        "visits_tx_center_year1",
        "visits_tx_center_year2_plus",
        "graft_survival_months",
        "patient_survival_months",
        "discontinued_graft_survival_months",
    }.union(egfr_fields, tpu_fields)

    int_fields = {"transplant_number", "donor_age"}

    bool_fields = {
        "lsp_flag",
        "aboi_flag",
        "np_tx_flag",
        "esp_flag",
        "delayed_graft_function",
        "on_hemodialysis",
        "on_peritoneal_dialysis",
        "graft_loss",
        "is_deceased",
        "discontinued_graft_loss",
        "maintenance_csa",
        "maintenance_tac",
        "maintenance_aza",
        "maintenance_mmf",
        "maintenance_rapamycin",
        "maintenance_prednisone",
        "acute_rejection_steroid_sensitive",
        "acute_rejection_steroid_resistant",
        "chronic_rejection",
        "recurrent_urinary_tract_infections",
        "cmv_infection",
        "cmv_disease",
        "malignancy",
    }

    string_fields = {
        "donor_gender",
        "donor_cause_of_death_category",
        "primary_disease",
        "primary_disease_category",
        "distance_category",
        "follow_up_by",
        "graft_loss_cause",
        "cause_of_death",
    }

    for _, row in df.iterrows():
        patient_hash = compute_patient_hash(row)
        transplant_id = _stringify_identifier(row.get("transplant_id"))
        assert transplant_id is not None, "transplant_id cannot be None"
        assert patient_hash is not None, "patient_hash cannot be None"

        patient_id_ntx = _stringify_identifier(row.get("patient_id_ntx"))
        patient_id_ukw = _stringify_identifier(row.get("patient_id_ukw"))

        optional_kwargs: Dict[str, Any] = {}
        for field_name in optional_fields:
            value = row.get(field_name)
            if field_name == "post_code":
                optional_kwargs[field_name] = _sanitize_post_code(value)
            elif field_name in float_fields:
                optional_kwargs[field_name] = _sanitize_numeric(value)
            elif field_name in int_fields:
                optional_kwargs[field_name] = _sanitize_numeric(value, as_int=True)
            elif field_name in bool_fields:
                optional_kwargs[field_name] = _sanitize_bool(value)
            elif field_name in string_fields:
                optional_kwargs[field_name] = _sanitize_text(value)
            else:
                optional_kwargs[field_name] = _sanitize(value)

        readout_data = ReadoutData(
            transplant_id=transplant_id,
            patient_id_ntx=patient_id_ntx,
            patient_id_ukw=patient_id_ukw,
            first_name=_sanitize(row.get("first_name")),
            last_name=_sanitize(row.get("last_name")),
            transplant_date=_sanitize(row.get("transplant_date")),
            dob=_sanitize(row.get("dob")),
            patient_hash=patient_hash,
            **optional_kwargs,
        )
        readout_data_list.append(readout_data)
    return readout_data_list


def serialize_fu_tx_distance_df(df: pd.DataFrame) -> List[ReadoutData]:
    fu_tx_distance_data_list: List[ReadoutData] = []
    for _, row in df.iterrows():
        patient_hash = compute_patient_hash(row)
        fu_tx_distance_data = ReadoutData(
            transplant_id=row["transplant_id"],
            patient_id_ntx=row["patient_id_ntx"],
            patient_id_ukw=row["patient_id_ukw"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            transplant_date=row["transplant_date"].isoformat() if not pd.isna(row["transplant_date"]) else "",
            dob=row["dob"].isoformat() if not pd.isna(row["dob"]) else "",
            patient_hash=patient_hash,
        )
        fu_tx_distance_data_list.append(fu_tx_distance_data)
    return fu_tx_distance_data_list


def create_lookup_patient_hash_for_transplant_id(patient_data_by_hash: Dict[str, PatientData]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for patient_hash, patient_data in patient_data_by_hash.items():
        transplant_ids = patient_data.transplant_ids
        for transplant_id in transplant_ids:
            lookup[transplant_id] = patient_hash
    return lookup


def create_lookup_patient_hash_for_patient_id_ukw(patient_data_by_hash: Dict[str, PatientData]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for patient_hash, patient_data in patient_data_by_hash.items():
        patient_ids_ukw = patient_data.patient_ids_ukw
        for patient_id_ukw in patient_ids_ukw:
            lookup[patient_id_ukw] = patient_hash

    return lookup


def create_case_to_patient_id_ukw_lookup(lab_data_list: List[LabData]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for lab_data in lab_data_list:
        lookup[lab_data.case_id_ukw] = lab_data.patient_id_ukw
    return lookup
