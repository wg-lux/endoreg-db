import yaml


def add_missing_classifications(examination_indication_file, classification_file):
    """
    Adds missing indication classifications from an examination indication file
    to an examination indication classification file.

    Args:
        examination_indication_file (str): Path to the examination indication YAML file.
        classification_file (str): Path to the examination indication classification YAML file.
    """

    with open(examination_indication_file, "r") as f:
        examination_data = yaml.safe_load(f)

    with open(classification_file, "r") as f:
        classification_data = yaml.safe_load(f)

    existing_classification_names = {
        item["fields"]["name"] for item in classification_data
    }

    new_classifications = []
    for item in examination_data:
        if "fields" in item and "classifications" in item["fields"]:
            for classification_name in item["fields"]["classifications"]:
                if classification_name not in existing_classification_names:
                    new_classifications.append(
                        {
                            "model": "endoreg_db.examination_indication_classification",
                            "fields": {
                                "name": classification_name,
                                "name_de": "TODO: Add German name",
                                "name_en": "TODO: Add English name",
                                "description": "TODO: Add description",
                                "examination": "TODO: Add examination",
                            },
                        }
                    )
                    existing_classification_names.add(classification_name)

    if new_classifications:
        classification_data.extend(new_classifications)

        with open(classification_file, "w") as f:
            yaml.dump(classification_data, f, indent=2, sort_keys=False)
        print(
            f"Added {len(new_classifications)} new classifications to "
            f"{classification_file}"
        )
    else:
        print("No new classifications to add.")


if __name__ == "__main__":
    examination_indication_file = "/home/admin/dev/endoreg-db/endoreg_db/data/examination_indication/endoscopy.yaml"
    classification_file = "/home/admin/dev/endoreg-db/endoreg_db/data/examination_indication_classification/endoscopy.yaml"
    add_missing_classifications(examination_indication_file, classification_file)
