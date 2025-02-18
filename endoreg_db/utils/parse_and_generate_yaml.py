import yaml
from pathlib import Path

# get this files path
file_path = Path(__file__)
module_root = file_path.parent.parent
data_dir = module_root / "data"


def collect_center_names(
):
    input_file_path = data_dir / "center/data.yaml"
    fist_name_dir = data_dir / "names_first"
    last_name_dir = data_dir / "names_last"
    # Load the input YAML file
    with open(input_file_path, 'r', encoding='utf-8') as file:
        data = yaml.safe_load(file)
    
    # Containers for first and last names
    first_names_set = set()
    last_names_set = set()
    
    # Extract first and last names from the YAML data
    for entry in data:
        fields = entry.get('fields', {})
        first_names_set.update(fields.get('first_names', []))
        last_names_set.update(fields.get('last_names', []))
    
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
    with open(fist_name_dir/"first_names.yaml", "w", encoding='utf-8') as first_file:
        yaml.dump(first_names_data, first_file, allow_unicode=True, sort_keys=False)
    
    with open(last_name_dir/"last_names.yaml", "w", encoding='utf-8') as last_file:
        yaml.dump(last_names_data, last_file, allow_unicode=True, sort_keys=False)

    # print("Generated first_names.yaml and last_names.yaml successfully.")
