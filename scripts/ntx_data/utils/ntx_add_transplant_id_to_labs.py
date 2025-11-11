from typing import Dict, List

import pandas as pd

from scripts.ntx_data.utils.datamodels import LabData, PatientData, ReadoutData
from scripts.ntx_data.utils.utils import processed_data_dir

post_code_distances_cache_path = processed_data_dir / "post_code_distances_cache copy.json"
lab_jsonl_data_path = processed_data_dir / "lab_data.jsonl"
patient_jsonl_data_path = processed_data_dir / "patient_data.jsonl"
readout_jsonl_data_path = processed_data_dir / "readout_data.jsonl"

lab_export_path = processed_data_dir / "lab_df_with_transplant_id.csv"


# Create a helper function to the following for each lab record:
# - get test_datetime
# - get patient_id_ntx
def get_lab_record_info(lab_record: LabData):
    test_datetime = lab_record.test_datetime
    patient_id_ntx = lab_record.patient_id_ntx
    return test_datetime, patient_id_ntx


def get_patient_data_for_lab_record(lab_record, patient_data_by_id_ntx: Dict[str, PatientData]):
    test_datetime, patient_id_ntx = get_lab_record_info(lab_record)
    if not patient_id_ntx:
        return None
    patient_data = patient_data_by_id_ntx.get(patient_id_ntx)
    if patient_data is None:
        return None
    return patient_data


def get_transplant_id_for_lab_record(lab_record, patient_data_by_id_ntx: Dict[str, PatientData], readout_data_by_transplant_id: Dict[str, ReadoutData]):
    test_datetime, patient_id_ntx = get_lab_record_info(lab_record)
    patient_data = get_patient_data_for_lab_record(lab_record, patient_data_by_id_ntx)
    if patient_data is None:
        return None

    transplant_ids = patient_data.transplant_ids
    if not transplant_ids:
        return None

    # get all transplant_ids and the tx_date from readout_data
    transplant_id_dates = []
    for transplant_id in transplant_ids:
        readout_data = readout_data_by_transplant_id.get(transplant_id)
        if readout_data is None:
            continue
        tx_date = readout_data.transplant_date
        transplant_id_dates.append((transplant_id, tx_date))

    # drop all transplant ids belonging to a date after the test date
    valid_transplant_ids = []
    for transplant_id, tx_date in transplant_id_dates:
        if tx_date is None or test_datetime is None:
            valid_transplant_ids.append(transplant_id)
        elif tx_date <= test_datetime:
            valid_transplant_ids.append(transplant_id)
    if not valid_transplant_ids:
        return None

    # If multiple valid transplant_ids, return the most recent one (max tx_date)
    if len(valid_transplant_ids) == 1:
        return valid_transplant_ids[0]
    else:
        valid_transplant_id_date_tuples = [
            (transplant_id, readout_data_by_transplant_id[transplant_id].transplant_date) for transplant_id in valid_transplant_ids
        ]

    # sort by date descending and return the first one
    valid_transplant_id_date_tuples.sort(key=lambda x: x[1] or "", reverse=True)
    return valid_transplant_id_date_tuples[0][0]


if __name__ == "__main__":
    from icecream import ic
    from tqdm import tqdm

    # importt jsonl files and create lists of datamodels

    # Serialize dataframes to datamodel lists
    ic("Serializing dataframes to datamodel lists...")

    lab_data_list: List[LabData] = []
    readout_data_list: List[ReadoutData] = []
    patient_data_list: List[PatientData] = []

    ic("Lab Data...")
    with open(lab_jsonl_data_path, "r") as f:
        for line in tqdm(f):
            lab_data_list.append(LabData.model_validate_json(line))

    ic("Patient Data...")
    with open(patient_jsonl_data_path, "r") as f:
        for line in tqdm(f):
            patient_data_list.append(PatientData.model_validate_json(line))

    ic("Readout Data...")
    with open(readout_jsonl_data_path, "r") as f:
        for line in tqdm(f):
            readout_data_list.append(ReadoutData.model_validate_json(line))

    # Create a Lookup to easily get patient_data by patient_id_ntx
    ic("Creating lookups...")
    patient_data_by_id_ntx = {patient_data.patient_id_ntx: patient_data for patient_data in patient_data_list}

    readout_data_by_transplant_id = {readout_data.transplant_id: readout_data for readout_data in readout_data_list}

    ic("Adding transplant_id to lab records...")
    for lab_data in tqdm(lab_data_list):
        transplant_id = get_transplant_id_for_lab_record(lab_data, patient_data_by_id_ntx, readout_data_by_transplant_id)
        lab_data.transplant_id = transplant_id

    # Export lab_data_list with transplant_id as jsonl
    jsonl_path = lab_export_path.with_suffix(".jsonl")
    ic(f"Exporting lab_data_list with transplant_id to {jsonl_path}...")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for lab_data in tqdm(lab_data_list):
            f.write(lab_data.model_dump_json())
            f.write("\n")

    # create pandas dataframe from lab_data_list
    ic("Creating pandas dataframe from lab_data_list...")
    lab_df_with_transplant_id = pd.DataFrame([lab_data.model_dump() for lab_data in lab_data_list])
    ic(f"Exporting lab dataframe with transplant_id to {lab_export_path}...")
    lab_df_with_transplant_id.to_csv(lab_export_path, index=False)

    # export as xlsx
    xlsx_path = lab_export_path.with_suffix(".xlsx")
    ic(f"Exporting lab dataframe with transplant_id to {xlsx_path}...")
    lab_df_with_transplant_id.to_excel(xlsx_path, index=False)
