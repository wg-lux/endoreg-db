from typing import List, Tuple

import pandas as pd

from .datamodels import ReadoutData
from .utils import (
    load_dataframe,
    readout_df_path,
    rename_readout_df_dict,
    serialize_readout_df,
)


def readout_df_etl() -> Tuple[pd.DataFrame, List[ReadoutData]]:
    readout_df = load_dataframe(readout_df_path, rename_dict=rename_readout_df_dict)
    readout_data_list: List[ReadoutData] = serialize_readout_df(readout_df)
    return readout_df, readout_data_list
