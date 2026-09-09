from __future__ import annotations

from typing import Literal

import pytest
from pytest import MonkeyPatch

import endoreg_db.utils.names as names_module
from gender_guesser.detector import Detector


type _DetectedGender = Literal[
    "unknown",
    "andy",
    "male",
    "female",
    "mostly_male",
    "mostly_female",
]
type _NormalizedGender = Literal["male", "female", "unknown"]


@pytest.mark.parametrize(
    ("detected", "expected"),
    [
        ("male", "male"),
        ("mostly_male", "male"),
        ("female", "female"),
        ("mostly_female", "female"),
        ("andy", "unknown"),
        ("unknown", "unknown"),
    ],
)
def test_guess_name_gender_normalizes_detector_contract(
    monkeypatch: MonkeyPatch,
    detected: _DetectedGender,
    expected: _NormalizedGender,
) -> None:
    def get_gender(
        _detector: Detector,
        _name: str,
        _country: str | None = None,
    ) -> _DetectedGender:
        return detected

    monkeypatch.setattr(Detector, "get_gender", get_gender)

    assert names_module.guess_name_gender("Example") == expected


def test_guess_name_gender_uses_library_unknown_result() -> None:
    assert names_module.guess_name_gender("__not_a_registered_given_name__") == (
        "unknown"
    )


def test_guess_name_gender_propagates_unexpected_detector_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    def get_gender(
        _detector: Detector,
        _name: str,
        _country: str | None = None,
    ) -> _DetectedGender:
        raise RuntimeError("broken detector data")

    monkeypatch.setattr(Detector, "get_gender", get_gender)

    with pytest.raises(RuntimeError, match="broken detector data"):
        names_module.guess_name_gender("Example")
