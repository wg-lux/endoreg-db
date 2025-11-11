from .utils.export import export2json
from .utils.process_fu_tx_distance import fu_tx_distance_df_etl
from .utils.process_lab_data import lab_data_etl
from .utils.process_patients import patient_df_etl
from .utils.process_readout import readout_df_etl
from .utils.utils import processed_data_dir

readout_df, readout_data_list = readout_df_etl()
lab_df, lab_data_list = lab_data_etl()

# patient_data_list = []
patient_data_list, readout_data_list, lab_data_list = patient_df_etl(readout_data_list=readout_data_list, lab_data_list=lab_data_list)

# fu_tx_data_list = []
fu_tx_data_list = fu_tx_distance_df_etl(patient_data_list)


# Export all DataFrames to a dictionary
# exported_dfs = export2dfs(
#     patient_data_list=patient_data_list,
#     readout_data_list=readout_data_list,
#     lab_data_list=lab_data_list,
#     fu_tx_data_list=fu_tx_data_list
# )

# for key, df in exported_dfs.items():
#     output_path = processed_data_dir / f"{key}.xlsx"
#     df.to_excel(output_path, index=False)

# # Additionally, export to csv
#     output_path_csv = processed_data_dir / f"{key}.csv"
#     df.to_csv(output_path_csv, index=False)

# Export all Data to JSONL strings
exported_jsonl = export2json(
    patient_data_list=patient_data_list, readout_data_list=readout_data_list, lab_data_list=lab_data_list, fu_tx_data_list=fu_tx_data_list
)

# write to jsonl files
for key, jsonl_str in exported_jsonl.items():
    output_path = processed_data_dir / f"{key}_data.jsonl"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(jsonl_str)
