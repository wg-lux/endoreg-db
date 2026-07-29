from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

from tracker import (
    Assessment,
    AssessmentStatus,
    Evidence,
    EvidenceKind,
    FeatureDefinition,
    FeatureTrackingState,
    REPOSITORY_ROOT,
    ReadinessStatus,
    TRACKING_DIR,
    TrackerError,
    Verification,
    VerificationCommand,
    VerificationKind,
    actively_tracked_features,
    derive_readiness,
    find_feature_references,
    guard_commit_message,
    load_feature_file,
    load_registry,
    load_registry_from_git_index,
    main,
    mark_feature_done,
    reopen_feature,
    run_verification,
    save_feature,
    update_assessment,
)


ASSESSMENT_TIME = datetime.fromisoformat("2026-07-17T10:00:00+00:00")


def _verified_feature(feature: FeatureDefinition) -> FeatureDefinition:
    verified = Assessment(
        status=AssessmentStatus.VERIFIED,
        evidence=(Evidence(kind=EvidenceKind.REVIEW, reference="review-42"),),
        assessed_by="reviewer@example.org",
        assessed_at=ASSESSMENT_TIME,
    )
    criteria = tuple(
        criterion.model_copy(update={"assessment": verified})
        for criterion in feature.definition_of_done
    )
    return FeatureDefinition(
        schema_version=feature.schema_version,
        id=feature.id,
        name=feature.name,
        description=feature.description,
        owners=feature.owners,
        production_critical=feature.production_critical,
        tracking=feature.tracking,
        source_documents=feature.source_documents,
        definition_of_done=criteria,
    )


def _unassessed_feature(feature: FeatureDefinition) -> FeatureDefinition:
    criteria = tuple(
        criterion.model_copy(update={"assessment": Assessment()})
        for criterion in feature.definition_of_done
    )
    return feature.model_copy(
        update={
            "source_documents": (),
            "definition_of_done": criteria,
        }
    )


def _run_git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_repository_registry_is_valid_and_tracks_current_assessments() -> None:
    _, features = load_registry(TRACKING_DIR)
    feature_paths = tuple(
        path
        for path in TRACKING_DIR.glob("*.yml")
        if path.name not in {"policy.yml", "schema.example.yml"}
    )

    assert len(features) == len(feature_paths)
    assert len({feature.id for feature in features}) == len(features)
    assert {feature.id for feature in features} >= {"standard", "type_safety"}
    for feature in features:
        readiness = derive_readiness(feature)
        assert 0 <= readiness.verified_required <= readiness.required_total
        assert 0 <= readiness.score_percent <= 100


def test_verified_status_requires_evidence_and_assessor() -> None:
    with pytest.raises(ValueError, match="require evidence"):
        Assessment(
            status=AssessmentStatus.VERIFIED,
            assessed_by="reviewer@example.org",
            assessed_at=ASSESSMENT_TIME,
        )


def test_all_required_criteria_must_be_verified_for_production() -> None:
    _, features = load_registry(TRACKING_DIR)
    feature = next(item for item in features if item.id == "dicom")
    ready = _verified_feature(feature)

    result = derive_readiness(ready)

    assert result.status is ReadinessStatus.PRODUCTION_READY
    assert result.score_percent == 100


def test_update_and_atomic_save_round_trip(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    feature = next(item for item in features if item.id == "dicom")
    updated = update_assessment(
        feature,
        criterion_id="documented_scope",
        status=AssessmentStatus.IN_PROGRESS,
        assessed_by="reviewer@example.org",
        note="Review läuft.",
    )

    destination = save_feature(updated, tmp_path)
    loaded = load_feature_file(destination)

    criterion = next(
        item for item in loaded.definition_of_done if item.id == "documented_scope"
    )
    assert criterion.assessment.status is AssessmentStatus.IN_PROGRESS
    assert criterion.assessment.assessed_by == "reviewer@example.org"
    assert not tuple(tmp_path.glob("*.tmp.*"))


def test_save_feature_preserves_existing_filename_case(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    feature = next(item for item in features if item.id == "dicom")
    existing = tmp_path / "DICOM.yml"
    existing.write_text("placeholder: true\n", encoding="utf-8")

    destination = save_feature(feature, tmp_path)

    assert destination == existing
    assert not (tmp_path / "dicom.yml").exists()
    assert load_feature_file(existing).id == "dicom"


def test_file_name_must_match_feature_id(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    payload = features[0].model_dump(mode="json", exclude_none=True)
    path = tmp_path / "wrong_name.yml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(TrackerError, match="Dateiname und Feature-ID"):
        load_feature_file(path)


def test_check_returns_failure_until_definition_of_done_is_verified(
    tmp_path: Path,
) -> None:
    policy, features = load_registry(TRACKING_DIR)
    source = next(item for item in features if item.id == "standard")
    feature = _unassessed_feature(source)
    tracking_dir = tmp_path / "feature-tracking"
    tracking_dir.mkdir()
    (tracking_dir / "policy.yml").write_text(
        yaml.safe_dump(
            policy.model_copy(update={"migrated_markdown_trackers": ()}).model_dump(
                mode="json"
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tracking_dir / "Standard.yml").write_text(
        yaml.safe_dump(feature.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    assert main(["--directory", str(tracking_dir), "check", "standard"]) == 1


def test_verify_update_requires_assessor_before_command_runs() -> None:
    with pytest.raises(TrackerError, match="erfordert --assessed-by"):
        main(["verify", "standard", "terminal_commands", "--update"])


def test_command_verification_requires_one_command_shape() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Verification(kind=VerificationKind.COMMAND)

    with pytest.raises(ValueError, match="exactly one"):
        Verification(
            kind=VerificationKind.COMMAND,
            command=("pytest",),
            commands=(VerificationCommand(command=("npm", "test")),),
        )


def test_verification_working_directory_must_be_absolute() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        VerificationCommand(
            command=("pytest",),
            working_directory="../lx-data-models",
        )


def test_multi_repository_verification_runs_shell_free_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_repository = tmp_path / "first"
    second_repository = tmp_path / "second"
    first_repository.mkdir()
    second_repository.mkdir()
    criterion = load_feature_file(TRACKING_DIR / "standard.yml").definition_of_done[
        0
    ]
    verification = Verification(
        kind=VerificationKind.COMMAND,
        commands=(
            VerificationCommand(
                command=("pytest", "tests/test_first.py"),
                working_directory=str(first_repository),
            ),
            VerificationCommand(
                command=("npm", "run", "test:unit"),
                working_directory=str(second_repository),
            ),
        ),
    )
    criterion = criterion.model_copy(update={"verification": verification})
    calls: list[tuple[tuple[str, ...], Path]] = []

    def fake_run(
        command: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert timeout == 300
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    successful, detail = run_verification(criterion)

    assert successful is True
    assert "tests/test_first.py" in detail
    assert "test:unit" in detail
    assert calls == [
        (("pytest", "tests/test_first.py"), first_repository),
        (("npm", "run", "test:unit"), second_repository),
    ]


def test_multi_repository_verification_stops_at_first_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    criterion = load_feature_file(TRACKING_DIR / "standard.yml").definition_of_done[
        0
    ]
    verification = Verification(
        kind=VerificationKind.COMMAND,
        commands=(
            VerificationCommand(command=("first",)),
            VerificationCommand(command=("must-not-run",)),
        ),
    )
    criterion = criterion.model_copy(update={"verification": verification})
    calls: list[tuple[str, ...]] = []

    def fake_run(
        command: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)

    successful, detail = run_verification(criterion)

    assert successful is False
    assert "Exit-Code 7" in detail
    assert calls == [("first",)]


def test_verify_update_records_each_atomic_command_as_evidence(
    tmp_path: Path,
) -> None:
    policy, features = load_registry(TRACKING_DIR)
    source = _unassessed_feature(
        next(item for item in features if item.id == "standard")
    )
    target = source.definition_of_done[0]
    target = target.model_copy(
        update={
            "verification": Verification(
                kind=VerificationKind.COMMAND,
                commands=(
                    VerificationCommand(
                        command=(sys.executable, "-c", "print('first')")
                    ),
                    VerificationCommand(
                        command=(sys.executable, "-c", "print('second')")
                    ),
                ),
            )
        }
    )
    feature = source.model_copy(
        update={
            "definition_of_done": (
                target,
                *source.definition_of_done[1:],
            )
        }
    )
    tracking_dir = tmp_path / "feature-tracking"
    tracking_dir.mkdir()
    (tracking_dir / "policy.yml").write_text(
        yaml.safe_dump(
            policy.model_copy(update={"migrated_markdown_trackers": ()}).model_dump(
                mode="json"
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tracking_dir / "standard.yml").write_text(
        yaml.safe_dump(feature.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    result = main(
        [
            "--directory",
            str(tracking_dir),
            "verify",
            "standard",
            target.id,
            "--update",
            "--assessed-by",
            "test@example.org",
        ]
    )

    assert result == 0
    updated = load_feature_file(tracking_dir / "standard.yml")
    assessment = updated.definition_of_done[0].assessment
    assert assessment.status is AssessmentStatus.VERIFIED
    assert [evidence.reference for evidence in assessment.evidence] == [
        shlex.join((sys.executable, "-c", "print('first')")),
        shlex.join((sys.executable, "-c", "print('second')")),
    ]


def test_tracker_governance_documentation_contract() -> None:
    readme = (TRACKING_DIR / "README.md").read_text(encoding="utf-8")
    agent_rules = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for required_readme_contract in (
        "./feature-tracking/tracker.py show",
        "./feature-tracking/tracker.py validate",
        "./feature-tracking/tracker.py check",
        "./feature-tracking/tracker.py update",
        "./feature-tracking/tracker.py verify",
        "./feature-tracking/tracker.py done",
        "./feature-tracking/tracker.py reopen",
        "Exit-Code `1`",
        "Exit-Code `2`",
        "atomaren und strukturiert protokollierten Dateioperationen",
        "Automatische Kommandos sind als Argumentliste gespeichert",
    ):
        assert required_readme_contract in readme
    assert "repositoryübergreifende" in readme.casefold()
    assert "Do not create or maintain parallel TODO" in agent_rules


def test_feature_references_match_ids_names_and_separator_variants() -> None:
    _, features = load_registry(TRACKING_DIR)

    matched = find_feature_references(
        "feat(dicom): align FHIR-R4-Export and audit-ledger",
        features,
    )

    assert {feature.id for feature in matched} == {"dicom", "fhir", "audit_ledger"}
    assert find_feature_references("documentation cleanup", features) == ()


def test_commit_guard_uses_staged_readiness_not_unstaged_yaml(
    tmp_path: Path,
) -> None:
    _, repository_features = load_registry(TRACKING_DIR)
    source = next(item for item in repository_features if item.id == "standard")
    feature = _unassessed_feature(source)
    policy, _ = load_registry(TRACKING_DIR)
    staged_policy = policy.model_copy(update={"migrated_markdown_trackers": ()})
    tracking_dir = tmp_path / "feature-tracking"
    tracking_dir.mkdir()
    (tracking_dir / "policy.yml").write_text(
        yaml.safe_dump(staged_policy.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    feature_path = tracking_dir / "Standard.yml"
    feature_path.write_text(
        yaml.safe_dump(feature.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    _run_git(tmp_path, "init", "--quiet")
    _run_git(tmp_path, "add", "feature-tracking")

    message_path = tmp_path / "COMMIT_EDITMSG"
    message_path.write_text("feat(standard): production release\n", encoding="utf-8")
    assert guard_commit_message(message_path, repository_root=tmp_path) == 1

    feature_path.write_text(
        yaml.safe_dump(
            _verified_feature(feature).model_dump(mode="json"), sort_keys=False
        ),
        encoding="utf-8",
    )
    _, indexed_features = load_registry_from_git_index(tmp_path)
    assert derive_readiness(indexed_features[0]).status is ReadinessStatus.EVALUATED
    assert guard_commit_message(message_path, repository_root=tmp_path) == 1

    _run_git(tmp_path, "add", feature_path.relative_to(tmp_path).as_posix())
    assert guard_commit_message(message_path, repository_root=tmp_path) == 0


def test_default_overview_marks_valid_features_as_evaluated(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, features = load_registry(TRACKING_DIR)
    active_count = len(actively_tracked_features(features))
    done_count = sum(
        feature.tracking.state is FeatureTrackingState.DONE for feature in features
    )

    assert main([]) == 0

    output = capsys.readouterr().out
    assert "evaluiert" in output
    assert f"Aktiv getrackt: {active_count}; Done: {done_count}" in output


def test_done_requires_complete_dod_and_excludes_feature_from_tracking() -> None:
    _, features = load_registry(TRACKING_DIR)
    source = next(item for item in features if item.id == "standard")
    feature = _unassessed_feature(source)

    with pytest.raises(TrackerError, match="kann nicht done gesetzt werden"):
        mark_feature_done(
            feature,
            changed_by="reviewer@example.org",
            note="Release freigegeben.",
        )

    completed = mark_feature_done(
        _verified_feature(feature),
        changed_by="reviewer@example.org",
        note="Release freigegeben.",
    )

    assert completed.tracking.state is FeatureTrackingState.DONE
    assert actively_tracked_features((completed,)) == ()
    assert find_feature_references("feat(standard): release", (completed,)) == ()

    reopened = reopen_feature(
        completed,
        criterion_id="defined_structure",
        changed_by="reviewer@example.org",
        note="Neue Anforderungen.",
    )
    assert reopened.tracking.state is FeatureTrackingState.ACTIVE
    assert actively_tracked_features((reopened,)) == (reopened,)
    assert len(reopened.tracking.history) == 2
    assert derive_readiness(reopened).status is ReadinessStatus.IN_PROGRESS
