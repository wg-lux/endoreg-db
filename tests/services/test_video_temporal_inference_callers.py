from __future__ import annotations

import pytest

from endoreg_db.services import video_temporal_inference as jobs


def test_extract_temporal_options_accepts_reimport_request_envelope() -> None:
    assert jobs.extract_temporal_options(
        {
            "refresh_predictions": True,
            "model_name": "default-model",
            "test_run": False,
            "threshold": 0.65,
        }
    ) == {"threshold": 0.65}


@pytest.mark.parametrize("value", ["", "   ", None])
def test_explicit_blank_temporal_model_does_not_fall_back(value: object) -> None:
    with pytest.raises(
        jobs.TemporalInferenceConfigError,
        match="temporal_model must be one of",
    ):
        jobs.normalize_temporal_options({"temporal_model": value})
