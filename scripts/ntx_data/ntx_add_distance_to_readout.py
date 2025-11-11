import json
from typing import List

import pandas as pd
from icecream import ic
from tqdm import tqdm

from scripts.ntx_data.utils.datamodels import ReadoutData
from scripts.ntx_data.utils.utils import processed_data_dir

post_code_distances_cache_path = processed_data_dir / "post_code_distances_cache copy.json"
readout_data_path = processed_data_dir / "readout_data.jsonl"
readout_df_excel_export_path = processed_data_dir / "readout_with_distances.xlsx"
readout_df_csv_export_path = processed_data_dir / "readout_with_distances.csv"
readout_df_jsonl_export_path = processed_data_dir / "readout_with_distances.jsonl"
readout_data_list: List[ReadoutData] = []

# EXAMPLE
# {
#   "36448": {
#     "distance_car_1": 151.953,
#     "distance_car_2": 163.49,
#     "distance_car_3": null,
#     "distance_car_mean": 157.721,
#     "distance_car_median": 157.721,
#     "distance_geographic": 117.807
#   },
#
# }
with open(post_code_distances_cache_path, "r", encoding="utf-8") as f:
    post_code_distances_cache = json.load(f)


with open(readout_data_path, "r", encoding="utf-8") as f:
    for line in tqdm(f):
        readout_data_list.append(ReadoutData.model_validate_json(line))

# Distance Categories
# < 50 km: "0-50 km"
# 50-100 km: "50-100 km"
# > 100 km: ">100 km"


def add_distances_to_readout_data(readout_data: ReadoutData, post_code_distances_cache: dict):
    plz = readout_data.post_code

    distance_dict = post_code_distances_cache.get(plz, None)
    if not distance_dict:
        return None
    distance = distance_dict["distance_car_mean"]

    distance_category = None
    if distance is not None:
        if distance < 50:
            distance_category = "0-50 km"
        elif 50 <= distance <= 100:
            distance_category = "50-100 km"
        else:
            distance_category = ">100 km"

    readout_data.distance_home_to_tx_center = distance
    readout_data.distance_category = distance_category


for readout_data in tqdm(readout_data_list):
    add_distances_to_readout_data(readout_data, post_code_distances_cache)

# dump to jsonl, csv, excel

with open(readout_df_jsonl_export_path, "w", encoding="utf-8") as f:
    for readout_data in tqdm(readout_data_list):
        f.write(readout_data.model_dump_json())
        f.write("\n")
ic(f"Exported readout data with distances to {readout_df_jsonl_export_path}.")

# create dataframe
readout_df = pd.DataFrame([readout.model_dump() for readout in readout_data_list])

ic(f"Exporting readout dataframe with distances to {readout_df_excel_export_path}...")
readout_df.to_excel(readout_df_excel_export_path, index=False)
ic(f"Exporting readout dataframe with distances to {readout_df_csv_export_path}...")
readout_df.to_csv(readout_df_csv_export_path, index=False)
