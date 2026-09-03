from .datamodels import LabData, PatientData, ReadoutData, TransplantData
from typing import List, Optional


def export2json(
    patient_data_list: Optional[List[PatientData]] = None,
    readout_data_list: Optional[List[ReadoutData]] = None,
    lab_data_list: Optional[List[LabData]] = None,
    fu_tx_data_list: Optional[List[TransplantData]] = None,
):
    """
    Export the processed data to JSONL files.
    All List fields of the supplied pydantic datamodels should be serialized as JSON arrays.
    """
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
    lab_jsonl = "\n".join([lab.model_dump_json() for lab in lab_data_list])
    fu_tx_jsonl = "\n".join([fu_tx.model_dump_json() for fu_tx in fu_tx_data_list])

    return {
        "patient": patient_jsonl,
        "readout": readout_jsonl,
        "lab": lab_jsonl,
        "fu_tx": fu_tx_jsonl,
    }
