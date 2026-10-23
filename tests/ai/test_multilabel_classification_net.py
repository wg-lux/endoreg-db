from __future__ import annotations

from pathlib import Path

import pytest

from endoreg_db.utils.ai.multilabel_classification_net import (
    MultiLabelClassificationNet,
)


def test_safetensors_loader_rejects_positional_constructor_arguments(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "model.safetensors"

    with pytest.raises(
        TypeError,
        match="Positional model arguments are not supported",
    ):
        MultiLabelClassificationNet.load_from_checkpoint(
            checkpoint_path,
            ["outside"],
            labels=["outside"],
        )
