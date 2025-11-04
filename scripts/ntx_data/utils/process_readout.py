from .utils import (
    load_dataframe,
    serialize_readout_df,
    readout_df_path,
    rename_readout_df_dict,
)
import pandas as pd
from .datamodels import ReadoutData, PatientData
from typing import List, Dict, Tuple
from .utils import compute_patient_hash




def readout_df_etl() -> Tuple[pd.DataFrame, List[ReadoutData]]:
    readout_df = load_dataframe(readout_df_path, rename_dict=rename_readout_df_dict)
    readout_data_list: List[ReadoutData] = serialize_readout_df(readout_df)
    return readout_df, readout_data_list