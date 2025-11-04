from . import (
    load_dataframe,
    # ,
    lab_df_path,
    rename_lab_df_dict,
    )
import pandas as pd
from .datamodels import LabData
from typing import List, Dict, Tuple
from . import compute_patient_hash, create_lookup_patient_hash_for_transplant_id


def lab_data_etl() -> Tuple[pd.DataFrame, List[LabData]]:
    lab_df = load_dataframe(lab_df_path, rename_dict=rename_lab_df_dict)
    
    data_list: List[LabData] = []
    for _, row in lab_df.iterrows():
        lab_data = LabData(
            patient_id_ntx=None,
            patient_id_ukw=str(row["patient_id_ukw"]),
            case_id_ukw=str(row["case_id_ukw"]),
            test_name=row["test_name"],
            test_value=row["test_value"],
            test_datetime=row["test_datetime"].isoformat() if not pd.isna(row["test_datetime"]) else "",
            
        )
        data_list.append(lab_data)

        
    return lab_df, data_list