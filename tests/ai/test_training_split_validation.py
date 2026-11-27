from __future__ import annotations

import pytest

from endoreg_db.utils.ai.model_training.config import TrainingConfig


@pytest.mark.parametrize(
    "validation,test",
    [
        (-0.1, 0.1),
        (0.1, -0.1),
        (1.0, 0.0),
        (0.0, 1.0),
        (0.5, 0.5),
        (0.8, 0.4),
        (float("nan"), 0.1),
        (0.1, float("nan")),
        (float("inf"), 0.1),
        (0.1, float("inf")),
        (True, 0.0),
        (0.0, False),
    ],
)
def test_training_config_rejects_invalid_split_ratios(
    validation: float, test: float
) -> None:
    with pytest.raises(ValueError):
        TrainingConfig(dataset_id=1, val_split=validation, test_split=test)


@pytest.mark.parametrize("validation,test", [(0.2, 0.1), (0.0, 0.0), (0.0, 0.5)])
def test_training_config_preserves_valid_split_ratios(
    validation: float, test: float
) -> None:
    config = TrainingConfig(dataset_id=1, val_split=validation, test_split=test)
    assert (config.val_split, config.test_split) == (validation, test)
