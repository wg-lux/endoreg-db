from typing import Dict, List, Tuple

from icecream import ic

from .datamodels import LabData, PatientData, ReadoutData
from .utils import (
    create_lookup_patient_hash_for_patient_id_ukw,
    load_dataframe,
    patient_df_path,
    rename_patient_df_dict,
    serialize_patient_df,
)


def sync_patient_readout_data(
    patient_data_list: List[PatientData], readout_data_list: List[ReadoutData]
) -> Tuple[List[PatientData], List[ReadoutData], List[ReadoutData]]:
    unmatched: List[ReadoutData] = []
    patient_data_by_hash: Dict[str, PatientData] = {}
    for patient_data in patient_data_list:
        patient_data_by_hash[patient_data.patient_hash] = patient_data

    for readout_data in readout_data_list:
        transplant_id = readout_data.transplant_id
        assert transplant_id is not None
        patient_hash = readout_data.patient_hash
        assert patient_hash is not None
        if patient_hash in patient_data_by_hash:
            patient_data_by_hash[patient_hash].transplant_ids.append(transplant_id)
            readout_data.patient_id_ntx = patient_data_by_hash[patient_hash].patient_id_ntx
        else:
            unmatched.append(readout_data)

    patient_data_list = list(patient_data_by_hash.values())

    return patient_data_list, readout_data_list, unmatched


def sync_patient_lab_data(patient_data_list: List[PatientData], lab_data_list: List[LabData]) -> Tuple[List[PatientData], List[LabData], List[LabData]]:
    unmatched: List[LabData] = []

    patient_data_by_hash: Dict[str, PatientData] = {patient_data.patient_hash: patient_data for patient_data in patient_data_list}

    patient_id_ukw_to_hash_lookup = create_lookup_patient_hash_for_patient_id_ukw(patient_data_by_hash=patient_data_by_hash)

    for lab_data in lab_data_list:
        patient_id_ukw = lab_data.patient_id_ukw
        case_id_ukw = lab_data.case_id_ukw
        patient_hash = patient_id_ukw_to_hash_lookup.get(patient_id_ukw, "")
        if patient_hash and patient_hash in patient_data_by_hash:
            patient_data_by_hash[patient_hash].case_ids_ukw.append(case_id_ukw)
            lab_data.patient_id_ntx = patient_data_by_hash[patient_hash].patient_id_ntx
        else:
            unmatched.append(lab_data)

    patient_data_list = list(patient_data_by_hash.values())

    return patient_data_list, lab_data_list, unmatched


def patient_df_etl(readout_data_list: List[ReadoutData], lab_data_list: List[LabData]) -> Tuple[List[PatientData], List[ReadoutData], List[LabData]]:
    patient_df = load_dataframe(patient_df_path, rename_dict=rename_patient_df_dict)
    patient_data_list: List[PatientData] = serialize_patient_df(patient_df)

    ic(f"Received {len(readout_data_list)} readout / transplant records.")
    ic(f"Received {len(lab_data_list)} lab records.")
    ic(f"Loaded {len(patient_data_list)} patient records.")

    ic("Adding transplant IDs to patients...")
    patient_data_list, readout_data_list, unmatched_readout = sync_patient_readout_data(
        patient_data_list=patient_data_list, readout_data_list=readout_data_list
    )

    _str = f"Total unmatched readout records: {len(unmatched_readout)}\n"
    _str += "Example unmatched readout records (up to 5):\n"
    for readout in unmatched_readout[:5]:
        _str += f"  transplant_id: {readout.transplant_id}, patient_id_ntx: {readout.patient_id_ntx}\n"
    ic(_str)

    # get number of unique transplant_ids in patient_data_list
    n_unique_transplant_ids = set()
    for patient in patient_data_list:
        for transplant_id in patient.transplant_ids:
            n_unique_transplant_ids.add(transplant_id)
    ic(f"Total unique transplant_ids in patient data: {len(n_unique_transplant_ids)}")

    ic("Adding case IDs to patients from lab data...")
    patient_data_list, lab_data_list, unmatched = sync_patient_lab_data(patient_data_list=patient_data_list, lab_data_list=lab_data_list)

    _str = f"Total unmatched lab records: {len(unmatched)}\n"
    _str += "Example unmatched lab records (up to 5):\n"
    for lab in unmatched[:5]:
        _str += f"  patient_id_ukw: {lab.patient_id_ukw}, case_id_ukw: {lab.case_id_ukw}\n"
    ic(_str)

    return patient_data_list, readout_data_list, lab_data_list
