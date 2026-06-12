from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypedDict, cast

import pandas as pd
from tqdm import tqdm

from scripts.ntx_data.utils.datamodels import LabData, ReadoutData
from scripts.ntx_data.utils.utils import processed_data_dir
from endoreg_db.utils.file_operations import atomic_write_file

lab_data_path = processed_data_dir / "lab_df_with_transplant_id.jsonl"
readout_data_path = processed_data_dir / "readout_with_distances.jsonl"
readout_df_excel_export_path = (
    processed_data_dir / "readout_with_distances_and_lab_counts.xlsx"
)
readout_df_csv_export_path = (
    processed_data_dir / "readout_with_distances_and_lab_counts.csv"
)
readout_df_jsonl_export_path = (
    processed_data_dir / "readout_with_distances_and_lab_counts.jsonl"
)
readout_data_list: list[ReadoutData] = []

lab_value_name = "Creatinin"
fu_years = [i for i in range(1, 21)]  # 1 to 20 years

with open(readout_data_path, "r", encoding="utf-8") as f:
    for line in tqdm(f):
        readout_data_list.append(ReadoutData.model_validate_json(line))

with open(lab_data_path, "r", encoding="utf-8") as f:
    lab_data_list: list[LabData] = []
    for line in tqdm(f):
        lab_data_list.append(LabData.model_validate_json(line))

# create dictionary: {patient_id_ntx: List[LabData]}
lab_data_by_patient_id_ntx: dict[str | None, list[LabData]] = {}
for lab_data in tqdm(lab_data_list):
    if lab_data.test_name != lab_value_name:
        continue

    if lab_data.patient_id_ntx not in lab_data_by_patient_id_ntx:
        lab_data_by_patient_id_ntx[lab_data.patient_id_ntx] = []

    lab_data_by_patient_id_ntx[lab_data.patient_id_ntx].append(lab_data)


# Create summary dict for further analyses
# We should iterate over the readout_data_list to initialize
# a dictionary with transplant_ids, transplant_dates and lab counts per follow-up year (empty at first)
# then, we create another loop over the lab_data_list to fill in the lab counts per follow-up year

# dt_format = "1949-04-09T00:00:00"  # ISO format
dt_format = "%Y-%m-%dT%H:%M:%S"  # ISO format


class FollowUpYearWindow(TypedDict):
    start_date: date
    end_date: date


class TransplantLabSummary(TypedDict):
    transplant_date: str
    fu_years: dict[str, FollowUpYearWindow]
    labs_by_fu_year: dict[str, list[LabData]]


def write_dataframe_to_excel(df: pd.DataFrame, path: Path) -> None:
    to_excel = cast(Callable[..., None], getattr(df, "to_excel"))
    to_excel(path, index=False)


def create_fu_years_dict(
    transplant_date: str,
    fu_years: list[int],
) -> dict[str, FollowUpYearWindow]:
    # transplant_date is ISO format string "YYYY-MM-DD"
    if not transplant_date:
        return {}

    transplant_date_dt = datetime.strptime(transplant_date, dt_format).date()

    fu_years_dict: dict[str, FollowUpYearWindow] = {}
    for year in fu_years:
        year_start_date = transplant_date_dt + timedelta(days=365 * (year - 1))
        year_end_date = (
            transplant_date_dt + timedelta(days=365 * year) - timedelta(days=1)
        )
        fu_years_dict[str(year)] = {
            "start_date": year_start_date,
            "end_date": year_end_date,
        }
    return fu_years_dict


# Example Structure
# {
# "transplant_id_1": {
#     "transplant_date": "YYYY-MM-DD",
#     "fu_years": {
#         "1": {
#             "start_date": "YYYY-MM-DD",
#             "end_date": "YYYY-MM-DD"
#              },
#              ...
#         },
#     "lab_counts_by_fu_year": {
#         "1": List[LabData]
#     }
# }
# }

summary_dict: dict[str, TransplantLabSummary] = {}

for readout_data in tqdm(readout_data_list):
    transplant_id = readout_data.transplant_id
    transplant_date = readout_data.transplant_date

    if not transplant_date:
        continue

    if transplant_id not in summary_dict:
        summary_dict[transplant_id] = {
            "transplant_date": transplant_date,
            "fu_years": create_fu_years_dict(transplant_date, fu_years),
            "labs_by_fu_year": {str(year): [] for year in fu_years},
        }

for lab_data in tqdm(lab_data_list):
    if lab_data.test_name != lab_value_name:
        continue

    transplant_id = lab_data.transplant_id
    if transplant_id is None:
        continue
    transplant_lab_summary = summary_dict.get(transplant_id)
    if not transplant_lab_summary:
        continue

    test_datetime_str = lab_data.test_datetime

    if not test_datetime_str:
        continue
    # e.g. 2014-11-27 12:08:00.000

    test_datetime = datetime.strptime(test_datetime_str, dt_format).date()
    assert test_datetime is not None

    for key, fu_year_info in transplant_lab_summary["fu_years"].items():
        start_date = fu_year_info["start_date"]
        end_date = fu_year_info["end_date"]
        if start_date <= test_datetime <= end_date:
            transplant_lab_summary["labs_by_fu_year"][key].append(lab_data)
            break

# export
records: list[dict[str, object]] = []
for key, value in summary_dict.items():
    record: dict[str, object] = {
        "transplant_id": key,
        "transplant_date": value["transplant_date"],
    }

    for year in fu_years:
        labs = value["labs_by_fu_year"][str(year)]
        record[f"lab_count_year_{year}"] = len(labs)
        record[f"unique_case_ids_year_{year}"] = len({lab.case_id_ukw for lab in labs})

    records.append(record)

    # create pandas dataframe from readout and summary, merge by transplant_id

readout_records: list[dict[str, Any]] = []
for readout_data in readout_data_list:
    record = readout_data.model_dump()
    readout_records.append(record)

readout_df = pd.DataFrame(readout_records)
summary_df = pd.DataFrame(records)

merged_df = pd.merge(readout_df, summary_df, on="transplant_id", how="left")

# write summary to jsonl
atomic_write_file(
    destination=readout_df_jsonl_export_path,
    content=(f"{row.to_json()}\n".encode("utf-8") for _, row in merged_df.iterrows()),
)

# export to excel and csv
write_dataframe_to_excel(merged_df, readout_df_excel_export_path)
merged_df.to_csv(readout_df_csv_export_path, index=False)
