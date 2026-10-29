import pandas as pd

from .datamodels import ReadoutData
from .utils import (
    load_dataframe,
    readout_df_path,
    rename_readout_df_dict,
    serialize_readout_df,
)


def readout_df_etl() -> tuple[pd.DataFrame, list[ReadoutData]]:
    readout_df = load_dataframe(readout_df_path, rename_dict=rename_readout_df_dict)
    readout_data_list: list[ReadoutData] = serialize_readout_df(readout_df)
    return readout_df, readout_data_list
