from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.check_documentation_governance import (
    ArtifactClass,
    CanonicalTopic,
    GovernancePolicy,
    Inventory,
    RepositoryPolicy,
    RelatedDocument,
    Severity,
    TemporaryException,
    classify_artifact,
    discover_repository,
    validate_inventory,
    validate_canonical_language,
    validate_local_links,
)


def _repository() -> RepositoryPolicy:
    return RepositoryPolicy(
        id="example",
        root_hint=".",
        docs_path="docs",
        readme_path="README.md",
        agents_path="AGENTS.md",
        documentation_entrypoint="docs/index.md",
        owner="example maintainers",
        canonical_language="en",
    )


def _policy(*exceptions: TemporaryException) -> GovernancePolicy:
    return GovernancePolicy(
        feature_id="documentation",
        baseline_date=date(2026, 7, 31),
        default_review_by=date(2026, 10, 31),
        repositories=[_repository()],
        canonical_topics=[],
        temporary_exceptions=list(exceptions),
    )


def _write_entrypoints(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "README.md").write_text("# Example\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# Agent guide\n", encoding="utf-8")
    (root / "docs" / "index.md").write_text("# Documentation\n", encoding="utf-8")


def test_classification_separates_sources_generated_and_sensitive_files() -> None:
    repository = _repository()

    assert (
        classify_artifact("docs/guide.md", repository) is ArtifactClass.SOURCE_DOCUMENT
    )
    assert (
        classify_artifact("docs/_build/html/index.html", repository)
        is ArtifactClass.GENERATED
    )
    assert (
        classify_artifact("docs/session.har", repository)
        is ArtifactClass.SENSITIVE_CAPTURE
    )
    assert (
        classify_artifact("docs/diagrams/model.mmd", repository)
        is ArtifactClass.DIAGRAM_SOURCE
    )


def test_discovery_assigns_complete_governance_metadata(tmp_path: Path) -> None:
    _write_entrypoints(tmp_path)
    guide = tmp_path / "docs" / "guide.md"
    guide.write_text("# Maintainer Guide\n", encoding="utf-8")

    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths={"README.md", "AGENTS.md", "docs/index.md", "docs/guide.md"},
    )

    discovered = next(item for item in entries if item.path == "docs/guide.md")
    assert discovered.title == "Maintainer Guide"
    assert discovered.owner == "example maintainers"
    assert discovered.canonical_source == "docs/guide.md"
    assert discovered.review_by == date(2026, 10, 31)
    assert discovered.wiki_slug == "example-docs-guide"
    assert discovered.tracked is True


def test_missing_agents_entrypoint_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# Example\n", encoding="utf-8")
    (tmp_path / "docs" / "index.md").write_text("# Docs\n", encoding="utf-8")
    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths=set(),
    )

    issues = validate_inventory(
        Inventory(generated_on=date(2026, 7, 31), entries=entries),
        _policy(),
        today=date(2026, 7, 31),
        available_repositories={"example"},
    )

    assert [(item.rule, item.path) for item in issues] == [
        ("missing_entrypoint", "AGENTS.md")
    ]


def test_tracked_generated_output_requires_active_timeboxed_exception(
    tmp_path: Path,
) -> None:
    _write_entrypoints(tmp_path)
    generated = tmp_path / "docs" / "_build" / "html" / "index.html"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated", encoding="utf-8")
    tracked = {"README.md", "AGENTS.md", "docs/index.md", "docs/_build/html/index.html"}
    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths=tracked,
    )
    exception = TemporaryException(
        id="generated_debt",
        repository="example",
        path_prefix="docs/_build/",
        rule="generated_file_tracked",
        owner="example maintainers",
        review_by=date(2026, 8, 31),
        exit_criteria="Remove generated files after packaging is fixed.",
    )

    active = validate_inventory(
        Inventory(generated_on=date(2026, 7, 31), entries=entries),
        _policy(exception),
        today=date(2026, 7, 31),
        available_repositories={"example"},
    )
    expired = validate_inventory(
        Inventory(generated_on=date(2026, 9, 1), entries=entries),
        _policy(exception),
        today=date(2026, 9, 1),
        available_repositories={"example"},
    )

    assert [item.severity for item in active] == [Severity.WARNING]
    assert active[0].exception_id == "generated_debt"
    assert [item.severity for item in expired] == [Severity.ERROR]


def test_tracked_sensitive_capture_fails_closed(tmp_path: Path) -> None:
    _write_entrypoints(tmp_path)
    capture = tmp_path / "docs" / "browser.har"
    capture.write_text('{"log": {}}', encoding="utf-8")
    tracked = {"README.md", "AGENTS.md", "docs/index.md", "docs/browser.har"}
    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths=tracked,
    )

    issues = validate_inventory(
        Inventory(generated_on=date(2026, 7, 31), entries=entries),
        _policy(),
        today=date(2026, 7, 31),
        available_repositories={"example"},
    )

    assert [(item.severity, item.rule) for item in issues] == [
        (Severity.ERROR, "sensitive_capture_tracked")
    ]


def test_canonical_topic_requires_canonical_and_related_documents(
    tmp_path: Path,
) -> None:
    _write_entrypoints(tmp_path)
    topic = CanonicalTopic(
        id="shared_contract",
        title="Shared contract",
        owner="example maintainers",
        canonical_repository="example",
        canonical_path="docs/missing-canonical.md",
        related_documents=[
            RelatedDocument(
                repository="example",
                path="docs/missing-component.md",
                role="Component-specific detail.",
            )
        ],
    )
    policy_data = _policy().model_dump()
    policy_data["canonical_topics"] = [topic.model_dump()]
    policy = GovernancePolicy.model_validate(policy_data)
    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths=set(),
    )

    issues = validate_inventory(
        Inventory(generated_on=date(2026, 7, 31), entries=entries),
        policy,
        today=date(2026, 7, 31),
        available_repositories={"example"},
    )

    assert [(item.rule, item.path) for item in issues] == [
        ("missing_canonical_document", "docs/missing-canonical.md"),
        ("missing_related_document", "docs/missing-component.md"),
    ]


def test_local_link_validation_rejects_missing_and_workstation_targets(
    tmp_path: Path,
) -> None:
    _write_entrypoints(tmp_path)
    guide = tmp_path / "docs" / "guide.md"
    guide.write_text(
        "[valid](index.md)\n[missing](missing.md)\n[local](/home/admin/private.md)\n",
        encoding="utf-8",
    )
    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths=set(),
    )

    issues = validate_local_links(tmp_path, _repository(), entries)

    assert [(item.rule, item.path) for item in issues] == [
        ("missing_local_link", "docs/guide.md"),
        ("workstation_link", "docs/guide.md"),
    ]


def test_additional_documentation_paths_are_inventoried(tmp_path: Path) -> None:
    _write_entrypoints(tmp_path)
    setup = tmp_path / "setup"
    setup.mkdir()
    guide = setup / "guide.md"
    guide.write_text("# Setup guide\n", encoding="utf-8")
    repository = _repository().model_copy(
        update={"additional_documentation_paths": ["setup"]}
    )

    entries = discover_repository(
        tmp_path,
        repository,
        review_by=date(2026, 10, 31),
        tracked_paths={"setup/guide.md"},
    )

    assert any(item.path == "setup/guide.md" for item in entries)


def test_canonical_language_rejects_predominantly_german_prose(
    tmp_path: Path,
) -> None:
    _write_entrypoints(tmp_path)
    guide = tmp_path / "docs" / "guide.md"
    guide.write_text(
        "# Betriebsanleitung\n\n"
        "Diese Anleitung ist für den Betrieb und die Prüfung der Anwendung. "
        "Sie muss mit der aktuellen Konfiguration geprüft werden und darf nicht "
        "ohne die dokumentierte Freigabe verwendet werden.\n",
        encoding="utf-8",
    )
    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths={"docs/guide.md"},
    )

    issues = validate_canonical_language(tmp_path, _repository(), entries)

    assert [(item.rule, item.path) for item in issues] == [
        ("non_english_source", "docs/guide.md")
    ]


def test_canonical_language_ignores_code_and_accepts_english_prose(
    tmp_path: Path,
) -> None:
    _write_entrypoints(tmp_path)
    guide = tmp_path / "docs" / "guide.md"
    guide.write_text(
        "# Operations guide\n\n"
        "This guide describes the current deployment contract and its checks.\n\n"
        "```text\nDer die das und oder nicht ist wird werden für mit ohne.\n```\n",
        encoding="utf-8",
    )
    entries = discover_repository(
        tmp_path,
        _repository(),
        review_by=date(2026, 10, 31),
        tracked_paths={"docs/guide.md"},
    )

    assert validate_canonical_language(tmp_path, _repository(), entries) == []
