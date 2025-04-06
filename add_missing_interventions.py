import yaml


def add_missing_interventions(examination_indication_file, finding_intervention_file):
    """
    Adds missing finding interventions from an examination indication file
    to a finding intervention file.

    Args:
        examination_indication_file (str): Path to the examination indication YAML file.
        finding_intervention_file (str): Path to the finding intervention YAML file.
    """

    with open(examination_indication_file, "r") as f:
        examination_data = yaml.safe_load(f)

    with open(finding_intervention_file, "r") as f:
        intervention_data = yaml.safe_load(f)

    existing_intervention_names = {item["fields"]["name"] for item in intervention_data}

    new_interventions = []
    for item in examination_data:
        if "fields" in item and "expected_interventions" in item["fields"]:
            for intervention_name in item["fields"]["expected_interventions"]:
                if intervention_name not in existing_intervention_names:
                    new_interventions.append(
                        {
                            "model": "endoreg_db.finding_intervention",
                            "fields": {
                                "name": intervention_name,
                                "description": "TODO: Add description",
                                "intervention_types": [],
                                "required_lab_values": [],
                                "contraindications": [],
                            },
                        }
                    )
                    existing_intervention_names.add(intervention_name)

    if new_interventions:
        intervention_data.extend(new_interventions)

        with open(finding_intervention_file, "w") as f:
            yaml.dump(intervention_data, f, indent=2, sort_keys=False)
        print(
            f"Added {len(new_interventions)} new interventions to "
            f"{finding_intervention_file}"
        )
    else:
        print("No new interventions to add.")


if __name__ == "__main__":
    examination_indication_file = "/home/admin/dev/endoreg-db/endoreg_db/data/examination_indication/endoscopy.yaml"
    finding_intervention_file = (
        "/home/admin/dev/endoreg-db/endoreg_db/data/finding_intervention/endoscopy.yaml"
    )
    add_missing_interventions(examination_indication_file, finding_intervention_file)
