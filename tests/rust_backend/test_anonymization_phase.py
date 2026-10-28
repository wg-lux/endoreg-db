from __future__ import annotations

import pytest

from endoreg_db.models.state.anonymization import (
    AnonymizationPhase,
    AnonymizationState,
    anonymization_phase,
    anonymization_states_in_phase,
)

EXPECTED_PHASES = {
    AnonymizationState.NOT_STARTED: AnonymizationPhase.NOT_STARTED,
    AnonymizationState.EXTRACTING_FRAMES: AnonymizationPhase.IN_PROGRESS,
    AnonymizationState.PROCESSING_ANONYMIZING: AnonymizationPhase.IN_PROGRESS,
    AnonymizationState.STARTED: AnonymizationPhase.IN_PROGRESS,
    AnonymizationState.ANONYMIZED: AnonymizationPhase.IN_PROGRESS,
    AnonymizationState.DONE_PROCESSING_ANONYMIZATION: (
        AnonymizationPhase.AWAITING_VALIDATION
    ),
    AnonymizationState.VALIDATED: AnonymizationPhase.VALIDATED,
    AnonymizationState.FAILED: AnonymizationPhase.FAILED,
}


def test_every_wire_status_has_a_native_phase() -> None:
    assert set(EXPECTED_PHASES) == set(AnonymizationState)


@pytest.mark.parametrize(("status", "phase"), EXPECTED_PHASES.items())
def test_native_phase_classification(
    status: AnonymizationState, phase: AnonymizationPhase
) -> None:
    assert anonymization_phase(status) is phase
    assert anonymization_phase(status.value) is phase


@pytest.mark.parametrize("phase", list(AnonymizationPhase))
def test_states_in_phase_partition_the_native_classification(
    phase: AnonymizationPhase,
) -> None:
    expected = {status for status, native in EXPECTED_PHASES.items() if native is phase}
    assert set(anonymization_states_in_phase(phase)) == expected


def test_states_in_several_phases_are_their_union() -> None:
    finished = anonymization_states_in_phase(
        AnonymizationPhase.AWAITING_VALIDATION, AnonymizationPhase.VALIDATED
    )
    assert set(finished) == {
        AnonymizationState.DONE_PROCESSING_ANONYMIZATION,
        AnonymizationState.VALIDATED,
    }


@pytest.mark.parametrize(
    "token", ["", "predicting_segments", "VALIDATED", "processing", "done"]
)
def test_unknown_status_token_fails_loudly(token: str) -> None:
    with pytest.raises(ValueError, match="unsupported anonymization status"):
        anonymization_phase(token)
