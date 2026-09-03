from __future__ import annotations

import yaml
from pathlib import Path
from collections.abc import Iterable
from typing import cast

from lx_dtypes.models.contracts.name_fixtures import (
    CenterNameFixturePayload,
    NameRecordPayload,
)

from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
)

# get this files path
file_path = Path(__file__)
module_root = file_path.resolve().parents[2]
data_dir = module_root / "data"


def _collect_fixture_names(
    data: Iterable[CenterNameFixturePayload],
) -> tuple[list[NameRecordPayload], list[NameRecordPayload]]:
    first_names_set: set[str] = set()
    last_names_set: set[str] = set()
    for entry in data:
        fields = entry.fields
        first_names_set.update(fields.first_names)
        last_names_set.update(fields.last_names)
    first_names_data = [
        NameRecordPayload(
            model="endoreg_db.first_name",
            fields={"name": name},
        )
        for name in sorted(first_names_set)
    ]
    last_names_data = [
        NameRecordPayload(
            model="endoreg_db.last_name",
            fields={"name": name},
        )
        for name in sorted(last_names_set)
    ]
    return first_names_data, last_names_data


def collect_center_names() -> None:
    input_file_path = data_dir / "center/data.yaml"
    fist_name_dir = data_dir / "names_first"
    last_name_dir = data_dir / "names_last"
    # Load the input YAML file
    with open(input_file_path, "r", encoding="utf-8") as file:
        data = cast(list[object], yaml.safe_load(file) or [])
    typed_data = [CenterNameFixturePayload.model_validate(item) for item in data]
    first_names_data, last_names_data = _collect_fixture_names(typed_data)

    # Write the data to separate YAML files
    ensure_directory(fist_name_dir)
    ensure_directory(last_name_dir)
    atomic_write_file(
        destination=fist_name_dir / "first_names.yaml",
        content=[
            yaml.dump(
                [item.model_dump(mode="python") for item in first_names_data],
                allow_unicode=True,
                sort_keys=False,
            ).encode("utf-8")
        ],
    )
    atomic_write_file(
        destination=last_name_dir / "last_names.yaml",
        content=[
            yaml.dump(
                [item.model_dump(mode="python") for item in last_names_data],
                allow_unicode=True,
                sort_keys=False,
            ).encode("utf-8")
        ],
    )

    # print("Generated first_names.yaml and last_names.yaml successfully.")
