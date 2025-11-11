from pathlib import Path

import pandas
import yaml

SOURCE_DIR = Path("./endoreg_db/data/_examples")
OUTPUT_FILE = SOURCE_DIR / "yaml_examples.xlsx"


def gather_yaml_data(source_dir):
    model_data = {}

    for yaml_file in source_dir.rglob("*.yaml"):
        with open(yaml_file, "r", encoding="utf-8") as f:
            for document in yaml.safe_load_all(f):
                if not document:
                    continue

                records = document if isinstance(document, list) else [document]

                for record in records:
                    if not isinstance(record, dict):
                        continue

                    model_name = record.get("model")
                    fields = record.get("fields")

                    if not model_name or not isinstance(fields, dict):
                        continue

                    normalized_fields = {key: ", ".join(map(str, value)) if isinstance(value, list) else value for key, value in fields.items()}

                    model_data.setdefault(model_name, []).append(normalized_fields)

    return model_data


def write_to_excel(model_data, output_file):
    if not model_data:
        raise ValueError("No model data found in the provided source directory.")

    sheet_names = {}

    with pandas.ExcelWriter(output_file, engine="openpyxl") as writer:
        for model_name in sorted(model_data):
            entries = model_data[model_name]
            if not entries:
                continue

            df = pandas.DataFrame(entries)

            base_name = model_name.replace(".", "_")
            sheet_name = base_name[:31]
            counter = 1

            # Ensure sheet names remain unique after truncation.
            while sheet_name in sheet_names:
                suffix = f"_{counter}"
                sheet_name = f"{base_name[: 31 - len(suffix)]}{suffix}"
                counter += 1

            sheet_names[sheet_name] = True
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def main():
    model_data = gather_yaml_data(SOURCE_DIR)
    write_to_excel(model_data, OUTPUT_FILE)
    print(f"Data written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
