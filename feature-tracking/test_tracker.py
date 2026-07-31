from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from threading import Barrier

import pytest
import yaml

from tracker import (
    AgentMessage,
    AgentMessageSeverity,
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
    acquire_feature_lock,
    acknowledge_agent_message,
    active_feature_locks,
    actively_tracked_features,
    agent_inbox,
    derive_readiness,
    find_feature_references,
    guard_commit_message,
    load_feature_file,
    load_registry,
    load_registry_from_git_index,
    main,
    mark_feature_done,
    reopen_feature,
    release_feature_lock,
    reply_to_agent_message,
    renew_feature_lock,
    run_verification,
    save_feature,
    send_agent_message,
    update_assessment,
    validate_feature_against_policy,
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
    active_tracking = feature.tracking.model_copy(
        update={
            "state": FeatureTrackingState.ACTIVE,
            "history": (),
        }
    )
    return feature.model_copy(
        update={
            "tracking": active_tracking,
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
        for path in (
            *TRACKING_DIR.glob("*.yml"),
            *(TRACKING_DIR / "done").glob("*.yml"),
        )
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


def test_verified_assessment_rejects_note_with_outstanding_work() -> None:
    policy, features = load_registry(TRACKING_DIR)
    feature = _verified_feature(features[0])
    criterion = feature.definition_of_done[0]
    contradictory = criterion.assessment.model_copy(
        update={"note": "Wheel- und Deployment-Abnahme stehen noch aus."}
    )
    replacement = criterion.model_copy(update={"assessment": contradictory})
    feature = feature.model_copy(
        update={
            "definition_of_done": (
                replacement,
                *feature.definition_of_done[1:],
            )
        }
    )

    with pytest.raises(TrackerError, match="ausstehende Pflichtarbeit"):
        validate_feature_against_policy(feature, policy)


def test_all_required_criteria_must_be_verified_for_production() -> None:
    _, features = load_registry(TRACKING_DIR)
    feature = next(item for item in features if item.id == "dicom")
    ready = _verified_feature(feature)

    result = derive_readiness(ready)

    assert result.status is ReadinessStatus.PRODUCTION_READY
    assert result.score_percent == 100


def test_update_and_atomic_save_round_trip(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    feature = _unassessed_feature(next(item for item in features if item.id == "dicom"))
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
    feature = _unassessed_feature(next(item for item in features if item.id == "dicom"))
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
    _, features = load_registry(TRACKING_DIR)
    feature = next(
        item
        for item in actively_tracked_features(features)
        if any(
            criterion.verification.kind is VerificationKind.COMMAND
            for criterion in item.definition_of_done
        )
    )
    criterion = next(
        item
        for item in feature.definition_of_done
        if item.verification.kind is VerificationKind.COMMAND
    )

    with pytest.raises(TrackerError, match="erfordert --assessed-by"):
        main(["verify", feature.id, criterion.id, "--update"])


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
    _, features = load_registry(TRACKING_DIR)
    criterion = next(
        item for item in features if item.id == "standard"
    ).definition_of_done[0]
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
    _, features = load_registry(TRACKING_DIR)
    criterion = next(
        item for item in features if item.id == "standard"
    ).definition_of_done[0]
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
    assert assessment.status is AssessmentStatus.IN_PROGRESS
    assert assessment.note is not None
    assert "Akzeptanzpunkte einzeln" in assessment.note
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
        "./feature-tracking/tracker.py message send",
        "./feature-tracking/tracker.py message inbox",
        "feature-tracking/.messages/",
        "Exit-Code `1`",
        "Exit-Code `2`",
        "atomaren und strukturiert protokollierten Dateioperationen",
        "Automatische Kommandos sind als Argumentliste gespeichert",
    ):
        assert required_readme_contract in readme
    assert "repositoryübergreifende" in readme.casefold()
    assert "Do not create or maintain parallel TODO" in agent_rules
    assert "tracker.py message inbox --owner <agent_id>" in agent_rules


def test_feature_references_match_ids_names_and_separator_variants() -> None:
    _, features = load_registry(TRACKING_DIR)

    matched = find_feature_references(
        "feat(dicom): align FHIR-R4-Export and audit-ledger",
        features,
    )

    assert {feature.id for feature in matched} == {"audit_ledger"}
    assert find_feature_references("chore: normalize whitespace", features) == ()


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


def test_save_feature_moves_done_and_reopened_definitions(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    source = _unassessed_feature(next(item for item in features if item.id == "dicom"))
    active_path = save_feature(source, tmp_path)
    completed = mark_feature_done(
        _verified_feature(source),
        changed_by="reviewer@example.org",
        note="Release freigegeben.",
    )

    done_path = save_feature(completed, tmp_path)

    assert done_path == tmp_path / "done" / active_path.name
    assert not active_path.exists()
    reopened = reopen_feature(
        completed,
        criterion_id="documented_scope",
        changed_by="reviewer@example.org",
        note="Neue Anforderungen.",
    )

    reopened_path = save_feature(reopened, tmp_path)

    assert reopened_path == active_path
    assert active_path.exists()
    assert not done_path.exists()


def test_feature_locks_reject_overlapping_scopes_and_allow_independent_work(
    tmp_path: Path,
) -> None:
    _, features = load_registry(TRACKING_DIR)
    feature = next(item for item in features if item.id == "standard")
    other_feature = next(
        item for item in actively_tracked_features(features) if item.id != feature.id
    )
    first_criterion = feature.definition_of_done[0].id
    second_criterion = feature.definition_of_done[1].id
    first = acquire_feature_lock(
        features,
        feature_id=feature.id,
        criterion_id=first_criterion,
        files=("feature-tracking/tracker.py",),
        owner="agent-one",
        directory=tmp_path,
    )

    with pytest.raises(TrackerError, match="kollidiert"):
        acquire_feature_lock(
            features,
            feature_id=other_feature.id,
            files=("feature-tracking/tracker.py",),
            owner="agent-two",
            directory=tmp_path,
        )

    independent = acquire_feature_lock(
        features,
        feature_id=feature.id,
        criterion_id=second_criterion,
        files=("feature-tracking/README.md",),
        owner="agent-two",
        directory=tmp_path,
    )

    assert {lock.lock_id for lock in active_feature_locks(tmp_path)} == {
        first.lock_id,
        independent.lock_id,
    }
    with pytest.raises(TrackerError, match="kollidiert"):
        acquire_feature_lock(
            features,
            feature_id=feature.id,
            owner="feature-owner",
            directory=tmp_path,
        )


def test_simultaneous_feature_lock_contenders_have_exactly_one_winner(
    tmp_path: Path,
) -> None:
    _, features = load_registry(TRACKING_DIR)
    barrier = Barrier(2)

    def contend(owner: str) -> str:
        barrier.wait()
        try:
            acquire_feature_lock(
                features,
                feature_id="standard",
                criterion_id="terminal_commands",
                owner=owner,
                directory=tmp_path,
            )
        except TrackerError as exc:
            assert "kollidiert" in str(exc)
            return "blocked"
        return "acquired"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(contend, ("agent-one", "agent-two")))

    assert sorted(outcomes) == ["acquired", "blocked"]
    assert len(active_feature_locks(tmp_path)) == 1


def test_feature_lock_expiry_renewal_and_owner_bound_release(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    acquired_at = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    acquired = acquire_feature_lock(
        features,
        feature_id="standard",
        criterion_id="terminal_commands",
        owner="agent-one",
        ttl_minutes=10,
        directory=tmp_path,
        now=acquired_at,
    )

    with pytest.raises(TrackerError, match="gehört"):
        renew_feature_lock(
            acquired.lock_id,
            owner="agent-two",
            directory=tmp_path,
            now=acquired_at + timedelta(minutes=1),
        )
    renewed = renew_feature_lock(
        acquired.lock_id,
        owner="agent-one",
        ttl_minutes=20,
        directory=tmp_path,
        now=acquired_at + timedelta(minutes=1),
    )
    assert renewed.expires_at == acquired_at + timedelta(minutes=21)
    with pytest.raises(TrackerError, match="gehört"):
        release_feature_lock(
            acquired.lock_id,
            owner="agent-two",
            directory=tmp_path,
        )

    assert active_feature_locks(tmp_path, now=acquired_at + timedelta(minutes=22)) == ()
    assert not tuple((tmp_path / ".locks").glob("*.json"))


def test_lock_cli_acquires_reports_and_releases(tmp_path: Path) -> None:
    policy, features = load_registry(TRACKING_DIR)
    feature = _unassessed_feature(
        next(item for item in features if item.id == "standard")
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

    assert (
        main(
            [
                "--directory",
                str(tracking_dir),
                "lock",
                "acquire",
                "standard",
                "--criterion",
                "terminal_commands",
                "--owner",
                "agent-one",
            ]
        )
        == 0
    )
    (lock,) = active_feature_locks(tracking_dir)
    assert main(["--directory", str(tracking_dir), "lock", "status", "standard"]) == 0
    assert (
        main(
            [
                "--directory",
                str(tracking_dir),
                "lock",
                "release",
                lock.lock_id,
                "--owner",
                "agent-one",
            ]
        )
        == 0
    )
    assert active_feature_locks(tracking_dir) == ()


def test_agent_messages_are_owner_bound_atomic_and_replyable(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    created_at = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    message = send_agent_message(
        features,
        sender="codex/manager",
        recipient="codex/worker-1",
        subject="Tracker evidence needs review",
        body="Replace the placeholder command before verification.",
        severity=AgentMessageSeverity.BLOCKING,
        feature_id="standard",
        criterion_id="terminal_commands",
        directory=tmp_path,
        now=created_at,
    )

    message_path = tmp_path / ".messages" / f"{message.message_id}.json"
    assert stat.S_IMODE(message_path.stat().st_mode) == 0o600
    assert agent_inbox(
        recipient="codex/worker-1",
        directory=tmp_path,
        now=created_at,
    ) == (message,)
    assert agent_inbox(
        recipient="codex/other",
        directory=tmp_path,
        now=created_at,
    ) == ()

    with pytest.raises(TrackerError, match="gehört"):
        acknowledge_agent_message(
            message.message_id,
            owner="codex/other",
            directory=tmp_path,
            now=created_at + timedelta(minutes=1),
        )

    acknowledged = acknowledge_agent_message(
        message.message_id,
        owner="codex/worker-1",
        directory=tmp_path,
        now=created_at + timedelta(minutes=1),
    )
    assert acknowledged.acknowledged_by == "codex/worker-1"
    assert agent_inbox(
        recipient="codex/worker-1",
        directory=tmp_path,
        now=created_at + timedelta(minutes=1),
    ) == ()

    reply = reply_to_agent_message(
        features,
        message.message_id,
        sender="codex/worker-1",
        body="Acknowledged; exact commands will be recorded.",
        directory=tmp_path,
        now=created_at + timedelta(minutes=2),
    )
    assert reply.recipient == "codex/manager"
    assert reply.reply_to == message.message_id
    assert reply.feature_id == "standard"
    assert reply.criterion_id == "terminal_commands"
    assert agent_inbox(
        recipient="codex/manager",
        directory=tmp_path,
        now=created_at + timedelta(minutes=2),
    ) == (reply,)


def test_agent_message_expiry_and_terminal_control_validation(tmp_path: Path) -> None:
    _, features = load_registry(TRACKING_DIR)
    created_at = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    message = send_agent_message(
        features,
        sender="manager",
        recipient="worker",
        subject="Short-lived review",
        body="Review this scope.",
        ttl_hours=1,
        directory=tmp_path,
        now=created_at,
    )

    assert agent_inbox(
        recipient="worker",
        directory=tmp_path,
        now=created_at + timedelta(hours=2),
    ) == ()
    assert not (tmp_path / ".messages" / f"{message.message_id}.json").exists()

    with pytest.raises(ValueError, match="control characters"):
        AgentMessage(
            message_id="a" * 32,
            sender="manager",
            recipient="worker",
            subject="Unsafe\x1b[31m",
            body="body",
            created_at=created_at,
            expires_at=created_at + timedelta(hours=1),
        )


def test_message_cli_and_lock_acquire_surface_unread_feedback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy, features = load_registry(TRACKING_DIR)
    feature = _unassessed_feature(
        next(item for item in features if item.id == "standard")
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

    assert main(
        [
            "--directory",
            str(tracking_dir),
            "message",
            "send",
            "--from",
            "codex/manager",
            "--to",
            "codex/worker",
            "--subject",
            "Please review the evidence",
            "--body",
            "Run the exact verifier before changing status.",
            "--feature",
            "standard",
            "--criterion",
            "terminal_commands",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "--directory",
            str(tracking_dir),
            "lock",
            "acquire",
            "standard",
            "--criterion",
            "terminal_commands",
            "--owner",
            "codex/worker",
        ]
    ) == 0
    lock_output = capsys.readouterr().out
    assert "Ungelesene Agentennachrichten: 1" in lock_output
    assert "Please review the evidence" in lock_output

    assert main(
        [
            "--directory",
            str(tracking_dir),
            "message",
            "inbox",
            "--owner",
            "codex/worker",
            "--json",
        ]
    ) == 0
    inbox_output = capsys.readouterr().out
    assert '"recipient": "codex/worker"' in inbox_output
