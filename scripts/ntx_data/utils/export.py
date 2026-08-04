from .datamodels import LabData, PatientData, ReadoutData, TransplantData
from typing import List, Optional
import pandas as pd

def export2json(
        patient_data_list: Optional[List[PatientData]] = None,
        readout_data_list: Optional[List[ReadoutData]] = None,
        lab_data_list: Optional[List[LabData]] = None,
        fu_tx_data_list: Optional[List[TransplantData]] = None
    ):
    '''
    Export the processed data to JSONL files.
    All List fields of the supplied pydantic datamodels should be serialized as JSON arrays.
    '''
    if patient_data_list is None:
        patient_data_list = []
    if readout_data_list is None:
        readout_data_list = []
    if lab_data_list is None:
        lab_data_list = []
    if fu_tx_data_list is None: 
        fu_tx_data_list = []

    patient_jsonl = "\n".join(
        [patient.model_dump_json() for patient in patient_data_list]
    )
    readout_jsonl = "\n".join(
        [readout.model_dump_json() for readout in readout_data_list]
    )
    lab_jsonl = "\n".join(
        [lab.model_dump_json() for lab in lab_data_list]
    )
    fu_tx_jsonl = "\n".join(
        [fu_tx.model_dump_json() for fu_tx in fu_tx_data_list]
    )

    return {
        "patient": patient_jsonl,
        "readout": readout_jsonl,
        "lab": lab_jsonl,
        "fu_tx": fu_tx_jsonl
    }

def export2dfs(
        patient_data_list: Optional[List[PatientData]] = None,
        readout_data_list: Optional[List[ReadoutData]] = None,
        lab_data_list: Optional[List[LabData]] = None,
        fu_tx_data_list: Optional[List[TransplantData]] = None
    ):
    '''
    Export the processed data to pandas Dataframes.
    All List fields of the supplied pydantic datamodels should be serialized as comma-separated strings.

    Additionally, create Lookup Dataframes from the PatientData Model:
    - patient_id_ntx to patient_ids_ukw
    - patient_id_ntx to transplant_ids
    - patient_id_ukw to case_ids_ukw
    '''
    # Export logic here
    if patient_data_list is None:
        patient_data_list = []
    if readout_data_list is None:
        readout_data_list = []
    if lab_data_list is None:
        lab_data_list = []
    if fu_tx_data_list is None: 
        fu_tx_data_list = []

    # dump all model lists to jsonl files


    patient_df = pd.DataFrame(
        [patient.model_dump() for patient in patient_data_list]
    )

    readout_df = pd.DataFrame(
        [readout.model_dump() for readout in readout_data_list]
    )

    lab_df = pd.DataFrame(
        [lab.model_dump() for lab in lab_data_list]
    )

    fu_tx_df = pd.DataFrame(
        [fu_tx.model_dump() for fu_tx in fu_tx_data_list]
    )

    lookup_patient_id_ukw_dict = []
    for patient in patient_data_list:
        patient_id_ntx = patient.patient_id_ntx
        for patient_id_ukw in patient.patient_ids_ukw:
            _entry = {
                "patient_id_ntx": patient_id_ntx,
                "patient_id_ukw": patient_id_ukw
            }
            lookup_patient_id_ukw_dict.append(_entry)
    lookup_patient_id_ukw_df = pd.DataFrame(lookup_patient_id_ukw_dict)

    lookup_transplant_id_dict = []
    for patient in patient_data_list:
        patient_id_ntx = patient.patient_id_ntx
        for transplant_id in patient.transplant_ids:
            _entry = {
                "patient_id_ntx": patient_id_ntx,
                "transplant_id": transplant_id
            }
            lookup_transplant_id_dict.append(_entry)
    lookup_transplant_id_df = pd.DataFrame(lookup_transplant_id_dict)

    lookup_case_id_ukw_dict = []
    for patient in patient_data_list:
        patient_id_ntx = patient.patient_id_ntx
        for case_id_ukw in patient.case_ids_ukw:
            _entry = {
                "patient_id_ntx": patient_id_ntx,
                "case_id_ukw": case_id_ukw
            }
            lookup_case_id_ukw_dict.append(_entry)
    lookup_case_id_ukw_df = pd.DataFrame(lookup_case_id_ukw_dict)

    return {
        "patient_df": patient_df,
        "readout_df": readout_df,
        "lab_df": lab_df,
        "fu_tx_df": fu_tx_df,
        "lookup_patient_id_ukw_df": lookup_patient_id_ukw_df,
        "lookup_transplant_id_df": lookup_transplant_id_df,
        "lookup_case_id_ukw_df": lookup_case_id_ukw_df
    }
