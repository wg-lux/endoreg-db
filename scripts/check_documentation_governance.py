from __future__ import annotations

import argparse
from datetime import date
from enum import StrEnum
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PROJECT_ROOT / "quality" / "documentation_governance.yml"
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)")
WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+")
FENCED_CODE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`]*`")
MARKDOWN_LINK_TARGET = re.compile(r"\]\([^)]*\)")

GERMAN_MARKERS = frozenset(
    {
        "aber",
        "alle",
        "als",
        "auch",
        "auf",
        "aus",
        "bei",
        "das",
        "dem",
        "den",
        "der",
        "des",
        "die",
        "dies",
        "diese",
        "durch",
        "ein",
        "eine",
        "einer",
        "eines",
        "für",
        "gegen",
        "ist",
        "kein",
        "keine",
        "mit",
        "muss",
        "müssen",
        "nach",
        "nicht",
        "nur",
        "oder",
        "sich",
        "sind",
        "über",
        "und",
        "von",
        "vor",
        "werden",
        "wird",
        "zu",
        "zum",
        "zur",
    }
)
ENGLISH_MARKERS = frozenset(
    {
        "a",
        "all",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "must",
        "not",
        "of",
        "on",
        "only",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)


class ArtifactClass(StrEnum):
    ENTRYPOINT = "entrypoint"
    SOURCE_DOCUMENT = "source_document"
    GENERATED = "generated"
    DIAGRAM_SOURCE = "diagram_source"
    RENDERED_DIAGRAM = "rendered_diagram"
    TEST_FIXTURE = "test_fixture"
    PUBLICATION_ARTIFACT = "publication_artifact"
    SENSITIVE_CAPTURE = "sensitive_capture"
    BINARY_ASSET = "binary_asset"
    DOCUMENTATION_CONFIG = "documentation_config"
    SUPPORTING_ARTIFACT = "supporting_artifact"


class Lifecycle(StrEnum):
    CURRENT = "current"
    REVIEW_DUE = "review_due"
    GENERATED = "generated"
    RESTRICTED = "restricted"


class Visibility(StrEnum):
    PUBLIC_CANDIDATE = "public_candidate"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class RepositoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    root_hint: str = Field(min_length=1)
    docs_path: str = Field(min_length=1)
    readme_path: str = Field(min_length=1)
    agents_path: str = Field(min_length=1)
    documentation_entrypoint: str | None
    owner: str = Field(min_length=1)
    canonical_language: Literal["en"] | None = None
    additional_documentation_paths: list[str] = Field(default_factory=list)


class RelatedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1)
    path: str = Field(min_length=1)
    role: str = Field(min_length=1)


class CanonicalTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    canonical_repository: str = Field(min_length=1)
    canonical_path: str = Field(min_length=1)
    related_documents: list[RelatedDocument]


class TemporaryException(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    repository: str = Field(min_length=1)
    path_prefix: str = Field(min_length=1)
    rule: Literal["generated_file_tracked"]
    owner: str = Field(min_length=1)
    review_by: date
    exit_criteria: str = Field(min_length=1)


def _empty_exceptions() -> list[TemporaryException]:
    return []


class GovernancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    feature_id: Literal["documentation"]
    baseline_date: date
    default_review_by: date
    repositories: list[RepositoryPolicy] = Field(min_length=1)
    canonical_topics: list[CanonicalTopic]
    temporary_exceptions: list[TemporaryException] = Field(
        default_factory=_empty_exceptions
    )

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "GovernancePolicy":
        repository_ids = [item.id for item in self.repositories]
        if repository_ids != sorted(repository_ids):
            raise ValueError("repositories must be sorted by id")
        if len(repository_ids) != len(set(repository_ids)):
            raise ValueError("repository ids must be unique")
        topic_ids = [item.id for item in self.canonical_topics]
        if topic_ids != sorted(topic_ids):
            raise ValueError("canonical topics must be sorted by id")
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("canonical topic ids must be unique")
        topic_repositories = {
            item.canonical_repository for item in self.canonical_topics
        } | {
            related.repository
            for item in self.canonical_topics
            for related in item.related_documents
        }
        unknown_topics = topic_repositories - set(repository_ids)
        if unknown_topics:
            raise ValueError(
                f"canonical topics reference unknown repositories: {sorted(unknown_topics)}"
            )
        exception_ids = [item.id for item in self.temporary_exceptions]
        if len(exception_ids) != len(set(exception_ids)):
            raise ValueError("temporary exception ids must be unique")
        unknown = {item.repository for item in self.temporary_exceptions} - set(
            repository_ids
        )
        if unknown:
            raise ValueError(
                f"exceptions reference unknown repositories: {sorted(unknown)}"
            )
        return self


class InventoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    path: str
    artifact_class: ArtifactClass
    title: str
    audience: str
    owner: str
    lifecycle: Lifecycle
    canonical_source: str | None
    visibility: Visibility
    last_reviewed: date | None
    review_by: date | None
    successor: str | None
    wiki_slug: str | None
    tracked: bool


class Inventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    feature_id: Literal["documentation"] = "documentation"
    generated_on: date
    entries: list[InventoryEntry]


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    rule: str
    repository: str
    path: str
    message: str
    exception_id: str | None = None


def load_policy(path: Path = DEFAULT_POLICY) -> GovernancePolicy:
    return GovernancePolicy.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def _tracked_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return {item.decode("utf-8") for item in result.stdout.split(b"\0") if item}


def classify_artifact(path: str, repository: RepositoryPolicy) -> ArtifactClass:
    if path in {repository.readme_path, repository.agents_path}:
        return ArtifactClass.ENTRYPOINT
    parts = Path(path).parts
    suffix = Path(path).suffix.lower()
    if "_build" in parts or "build" in parts:
        return ArtifactClass.GENERATED
    if "fixtures" in parts:
        return ArtifactClass.TEST_FIXTURE
    if suffix == ".har":
        return ArtifactClass.SENSITIVE_CAPTURE
    if "publication" in parts and suffix in {".docx", ".pdf"}:
        return ArtifactClass.PUBLICATION_ARTIFACT
    if suffix == ".mmd":
        return ArtifactClass.DIAGRAM_SOURCE
    if "diagrams" in parts and suffix in {".png", ".svg"}:
        return ArtifactClass.RENDERED_DIAGRAM
    if suffix in {".md", ".rst"}:
        return ArtifactClass.SOURCE_DOCUMENT
    if Path(path).name in {"Makefile", "make.bat", "conf.py"}:
        return ArtifactClass.DOCUMENTATION_CONFIG
    if suffix in {".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp"}:
        return ArtifactClass.BINARY_ASSET
    return ArtifactClass.SUPPORTING_ARTIFACT


def _text_title(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return path.name
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
        if stripped and index + 1 < len(lines):
            underline = lines[index + 1].strip()
            if underline and set(underline) <= {"=", "-", "~", "^"}:
                return stripped
    return path.stem.replace("_", " ").replace("-", " ").strip().title()


def _wiki_slug(repository: str, path: str) -> str:
    stem = str(Path(path).with_suffix(""))
    value = re.sub(r"[^a-z0-9]+", "-", f"{repository}-{stem}".lower())
    return value.strip("-")


def _canonical_source(
    root: Path,
    relative_path: str,
    artifact_class: ArtifactClass,
) -> str | None:
    if artifact_class in {ArtifactClass.ENTRYPOINT, ArtifactClass.SOURCE_DOCUMENT}:
        return relative_path
    if artifact_class is ArtifactClass.RENDERED_DIAGRAM:
        source = Path(relative_path).with_suffix(".mmd")
        if (root / source).is_file():
            return source.as_posix()
    return None


def build_entry(
    *,
    root: Path,
    relative_path: str,
    repository: RepositoryPolicy,
    tracked_paths: set[str],
    review_by: date,
) -> InventoryEntry:
    artifact_class = classify_artifact(relative_path, repository)
    restricted = artifact_class in {
        ArtifactClass.PUBLICATION_ARTIFACT,
        ArtifactClass.SENSITIVE_CAPTURE,
    }
    generated = artifact_class is ArtifactClass.GENERATED
    public_candidate = artifact_class in {
        ArtifactClass.ENTRYPOINT,
        ArtifactClass.SOURCE_DOCUMENT,
    }
    lifecycle = (
        Lifecycle.RESTRICTED
        if restricted
        else Lifecycle.GENERATED
        if generated
        else Lifecycle.REVIEW_DUE
        if public_candidate
        else Lifecycle.CURRENT
    )
    visibility = (
        Visibility.RESTRICTED
        if restricted
        else Visibility.PUBLIC_CANDIDATE
        if public_candidate
        else Visibility.INTERNAL
    )
    return InventoryEntry(
        repository=repository.id,
        path=relative_path,
        artifact_class=artifact_class,
        title=_text_title(root / relative_path),
        audience="contributors" if public_candidate else "maintainers",
        owner=repository.owner,
        lifecycle=lifecycle,
        canonical_source=_canonical_source(root, relative_path, artifact_class),
        visibility=visibility,
        last_reviewed=None,
        review_by=review_by if public_candidate else None,
        successor=None,
        wiki_slug=(
            _wiki_slug(repository.id, relative_path)
            if artifact_class is ArtifactClass.SOURCE_DOCUMENT
            else None
        ),
        tracked=relative_path in tracked_paths,
    )


def discover_repository(
    root: Path,
    repository: RepositoryPolicy,
    *,
    review_by: date,
    tracked_paths: set[str] | None = None,
) -> list[InventoryEntry]:
    paths: set[str] = set()
    for entrypoint in (repository.readme_path, repository.agents_path):
        if (root / entrypoint).is_file():
            paths.add(entrypoint)
    docs_root = root / repository.docs_path
    if docs_root.is_dir():
        paths.update(
            path.relative_to(root).as_posix()
            for path in docs_root.rglob("*")
            if path.is_file()
        )
    for configured_path in repository.additional_documentation_paths:
        candidate = root / configured_path
        if candidate.is_file():
            paths.add(candidate.relative_to(root).as_posix())
        elif candidate.is_dir():
            paths.update(
                path.relative_to(root).as_posix()
                for path in candidate.rglob("*")
                if path.is_file() and path.suffix.lower() in {".md", ".rst"}
            )
    tracked = _tracked_paths(root) if tracked_paths is None else tracked_paths
    return [
        build_entry(
            root=root,
            relative_path=path,
            repository=repository,
            tracked_paths=tracked,
            review_by=review_by,
        )
        for path in sorted(paths)
    ]


def _matching_exception(
    entry: InventoryEntry,
    policy: GovernancePolicy,
    *,
    rule: str,
) -> TemporaryException | None:
    return next(
        (
            item
            for item in policy.temporary_exceptions
            if item.repository == entry.repository
            and item.rule == rule
            and entry.path.startswith(item.path_prefix)
        ),
        None,
    )


def validate_inventory(
    inventory: Inventory,
    policy: GovernancePolicy,
    *,
    today: date,
    available_repositories: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    by_repository: dict[str, set[str]] = {}
    for entry in inventory.entries:
        by_repository.setdefault(entry.repository, set()).add(entry.path)
        if entry.artifact_class is ArtifactClass.GENERATED and entry.tracked:
            exception = _matching_exception(
                entry,
                policy,
                rule="generated_file_tracked",
            )
            exception_active = exception is not None and exception.review_by >= today
            issues.append(
                ValidationIssue(
                    severity=Severity.WARNING if exception_active else Severity.ERROR,
                    rule="generated_file_tracked",
                    repository=entry.repository,
                    path=entry.path,
                    message=(
                        "generated documentation is tracked under a temporary exception"
                        if exception_active
                        else "generated documentation must not be tracked"
                    ),
                    exception_id=exception.id if exception is not None else None,
                )
            )
        if entry.artifact_class is ArtifactClass.SENSITIVE_CAPTURE and entry.tracked:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    rule="sensitive_capture_tracked",
                    repository=entry.repository,
                    path=entry.path,
                    message="sensitive diagnostic captures must not be tracked in docs",
                )
            )
    for repository in policy.repositories:
        if repository.id not in available_repositories:
            continue
        present = by_repository.get(repository.id, set())
        required = [repository.readme_path, repository.agents_path]
        if repository.documentation_entrypoint is not None:
            required.append(repository.documentation_entrypoint)
        for path in required:
            if path not in present:
                issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        rule="missing_entrypoint",
                        repository=repository.id,
                        path=path,
                        message="required documentation entrypoint is missing",
                    )
                )
    inventory_paths = {
        (entry.repository, entry.path): entry for entry in inventory.entries
    }
    for topic in policy.canonical_topics:
        if topic.canonical_repository not in available_repositories:
            continue
        canonical_key = (topic.canonical_repository, topic.canonical_path)
        canonical = inventory_paths.get(canonical_key)
        if canonical is None:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    rule="missing_canonical_document",
                    repository=topic.canonical_repository,
                    path=topic.canonical_path,
                    message=f"canonical document for topic {topic.id!r} is missing",
                )
            )
        elif canonical.artifact_class not in {
            ArtifactClass.ENTRYPOINT,
            ArtifactClass.SOURCE_DOCUMENT,
        }:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    rule="invalid_canonical_document",
                    repository=topic.canonical_repository,
                    path=topic.canonical_path,
                    message=f"canonical topic {topic.id!r} does not reference a source document",
                )
            )
        for related in topic.related_documents:
            if related.repository not in available_repositories:
                continue
            if (related.repository, related.path) not in inventory_paths:
                issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        rule="missing_related_document",
                        repository=related.repository,
                        path=related.path,
                        message=f"related document for topic {topic.id!r} is missing",
                    )
                )
    return sorted(issues, key=lambda item: (item.severity, item.repository, item.path))


def validate_local_links(
    root: Path,
    repository: RepositoryPolicy,
    entries: list[InventoryEntry],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for entry in entries:
        if Path(entry.path).suffix.lower() != ".md":
            continue
        source = root / entry.path
        try:
            content = source.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in MARKDOWN_LINK.finditer(content):
            raw_target = match.group("target").strip("<>")
            if raw_target.startswith("/home/") or raw_target.startswith("file://"):
                issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        rule="workstation_link",
                        repository=repository.id,
                        path=entry.path,
                        message=f"workstation-specific Markdown link: {raw_target}",
                    )
                )
                continue
            parsed = urlsplit(raw_target)
            if parsed.scheme or parsed.netloc or raw_target.startswith(("#", "/")):
                continue
            relative_target = unquote(parsed.path)
            if not relative_target or "{" in relative_target:
                continue
            resolved = (source.parent / relative_target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        rule="link_outside_repository",
                        repository=repository.id,
                        path=entry.path,
                        message=f"relative Markdown link leaves repository: {raw_target}",
                    )
                )
                continue
            if not resolved.exists():
                issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        rule="missing_local_link",
                        repository=repository.id,
                        path=entry.path,
                        message=f"local Markdown link target is missing: {raw_target}",
                    )
                )
    return issues


def _prose_words(content: str) -> list[str]:
    prose_lines: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if FENCED_CODE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            prose_lines.append(line)
    prose = "\n".join(prose_lines)
    prose = INLINE_CODE.sub(" ", prose)
    prose = MARKDOWN_LINK_TARGET.sub("]", prose)
    return [match.group(0).casefold() for match in WORD.finditer(prose)]


def _is_predominantly_german(content: str) -> bool:
    words = _prose_words(content)
    german_count = sum(word in GERMAN_MARKERS for word in words)
    english_count = sum(word in ENGLISH_MARKERS for word in words)
    return german_count >= 8 and german_count > english_count


def validate_canonical_language(
    root: Path,
    repository: RepositoryPolicy,
    entries: list[InventoryEntry],
) -> list[ValidationIssue]:
    if repository.canonical_language is None:
        return []
    issues: list[ValidationIssue] = []
    for entry in entries:
        if entry.artifact_class not in {
            ArtifactClass.ENTRYPOINT,
            ArtifactClass.SOURCE_DOCUMENT,
        }:
            continue
        source = root / entry.path
        try:
            content = source.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _is_predominantly_german(content):
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    rule="non_english_source",
                    repository=repository.id,
                    path=entry.path,
                    message="canonical documentation must be written in English",
                )
            )
    return issues


def _parse_overrides(values: list[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        repository, separator, raw_path = value.partition("=")
        if not separator or not repository or not raw_path:
            raise ValueError(f"invalid --repo-root value: {value!r}")
        overrides[repository] = Path(raw_path).expanduser().resolve()
    return overrides


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--repo-root", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--repository", action="append", default=[], metavar="ID")
    parser.add_argument("--allow-missing-repositories", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    policy = load_policy(args.policy)
    overrides = _parse_overrides(args.repo_root)
    selected = set(args.repository)
    unknown = selected - {item.id for item in policy.repositories}
    if unknown:
        raise ValueError(f"unknown repositories: {sorted(unknown)}")
    entries: list[InventoryEntry] = []
    link_issues: list[ValidationIssue] = []
    available: set[str] = set()
    for repository in policy.repositories:
        if selected and repository.id not in selected:
            continue
        root = overrides.get(
            repository.id,
            (PROJECT_ROOT / repository.root_hint).resolve(),
        )
        if not root.is_dir():
            if args.allow_missing_repositories:
                continue
            print(
                f"ERROR {repository.id}: repository root does not exist: {root}",
                file=sys.stderr,
            )
            return 2
        available.add(repository.id)
        repository_entries = discover_repository(
            root,
            repository,
            review_by=policy.default_review_by,
        )
        entries.extend(repository_entries)
        link_issues.extend(validate_local_links(root, repository, repository_entries))
        link_issues.extend(
            validate_canonical_language(root, repository, repository_entries)
        )
    inventory = Inventory(generated_on=date.today(), entries=entries)
    issues = (
        validate_inventory(
            inventory,
            policy,
            today=date.today(),
            available_repositories=available,
        )
        + link_issues
    )
    issues.sort(key=lambda item: (item.severity, item.repository, item.path, item.rule))
    payload = {
        "inventory": inventory.model_dump(mode="json"),
        "issues": [item.model_dump(mode="json") for item in issues],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    if args.format == "json":
        if args.output is None:
            print(rendered, end="")
    else:
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.repository] = counts.get(entry.repository, 0) + 1
        for repository, count in sorted(counts.items()):
            print(f"{repository}: {count} inventory entries")
        errors = [item for item in issues if item.severity is Severity.ERROR]
        warnings = [item for item in issues if item.severity is Severity.WARNING]
        for issue in [*errors[:20], *warnings[:20]]:
            print(
                f"{issue.severity.value.upper()} {issue.repository}/{issue.path}: "
                f"{issue.message}"
            )
        displayed = min(len(errors), 20) + min(len(warnings), 20)
        if len(issues) > displayed:
            print(f"... {len(issues) - displayed} additional issues in JSON output")
        print(
            f"documentation governance: {len(errors)} error(s), {len(warnings)} warning(s)"
        )
    return 1 if any(item.severity is Severity.ERROR for item in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
