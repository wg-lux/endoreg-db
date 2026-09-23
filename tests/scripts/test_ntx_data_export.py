from __future__ import annotations

import json

from scripts.ntx_data.utils.datamodels import ReadoutData
from scripts.ntx_data.utils.export import export2json


def test_export2json_serializes_each_collection_as_json_lines() -> None:
    # Arrange
    readout = ReadoutData(transplant_id="tx-1", patient_id_ntx="patient-1")

    # Act
    result = export2json(readout_data_list=[readout])

    # Assert
    assert set(result) == {"patient", "readout", "lab", "fu_tx"}
    assert json.loads(result["readout"]) == readout.model_dump(mode="json")
    assert result["patient"] == result["lab"] == result["fu_tx"] == ""


def test_export2json_uses_empty_collections_when_arguments_are_omitted() -> None:
    # Arrange / Act
    result = export2json()

    # Assert
    assert result == {"patient": "", "readout": "", "lab": "", "fu_tx": ""}
