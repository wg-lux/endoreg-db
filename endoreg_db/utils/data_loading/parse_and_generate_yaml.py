import yaml
from pathlib import Path

from endoreg_db.utils.filesystem.file_operations import (
    atomic_write_file,
    ensure_directory,
)

# get this files path
file_path = Path(__file__)
module_root = file_path.resolve().parents[2]
data_dir = module_root / "data"


def collect_center_names():
    input_file_path = data_dir / "center/data.yaml"
    fist_name_dir = data_dir / "names_first"
    last_name_dir = data_dir / "names_last"
    # Load the input YAML file
    with open(input_file_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    # Containers for first and last names
    first_names_set = set()
    last_names_set = set()

    # Extract first and last names from the YAML data
    for entry in data:
        fields = entry.get("fields", {})
        first_names_set.update(fields.get("first_names", []))
        last_names_set.update(fields.get("last_names", []))

    # Create a list of dictionaries for first and last names
    first_names_data = [
        {"model": "endoreg_db.first_name", "fields": {"name": name}}
        for name in sorted(first_names_set)
    ]
    last_names_data = [
        {"model": "endoreg_db.last_name", "fields": {"name": name}}
        for name in sorted(last_names_set)
    ]

    # Write the data to separate YAML files
    ensure_directory(fist_name_dir)
    ensure_directory(last_name_dir)
    atomic_write_file(
        destination=fist_name_dir / "first_names.yaml",
        content=[
            yaml.dump(first_names_data, allow_unicode=True, sort_keys=False).encode(
                "utf-8"
            )
        ],
    )
    atomic_write_file(
        destination=last_name_dir / "last_names.yaml",
        content=[
            yaml.dump(last_names_data, allow_unicode=True, sort_keys=False).encode(
                "utf-8"
            )
        ],
    )

    # print("Generated first_names.yaml and last_names.yaml successfully.")
