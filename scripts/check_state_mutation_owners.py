"""Freeze which modules may write lifecycle status and state fields.

Every write in ``endoreg_db`` to a field named ``status``, ``state``,
``*_status``, ``*_state`` or ``failure_class`` must be listed in
``quality/state_mutation_owners.yml`` with its state machine and a reviewed
writer kind. A write counts when it is an attribute assignment, a
``setattr`` with a literal name, a keyword of an ``update(...)`` call, or a
literal field of ``bulk_update(...)``. Row creation is not a mutation and is
not scanned.

The check fails on unlisted writers, on listed writers that no longer exist,
and on a second writer of a field that has a native single writer. See
``docs/state_mutation_ownership.md``.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OWNERS = PROJECT_ROOT / "quality" / "state_mutation_owners.yml"
SCANNED_PACKAGE = "endoreg_db"
WATCHED_FIELD = re.compile(r"^(?:\w+_)?(?:status|state)$|^failure_class$")


class WriterKind(StrEnum):
    """Reviewed safety level of one module's writes to a state machine."""

    # The only writer of the field: locks the row and asks a native table.
    NATIVE_SINGLE_WRITER = "native_single_writer"
    # Writes under a row lock after native transition validation.
    NATIVE_VALIDATED = "native_validated"
    # Model method that only its owning locked lifecycle service calls.
    MODEL_TRANSITION_METHOD = "model_transition_method"
    # Writes under a row lock or conditional update with Python-only checks.
    LOCKED = "locked"
    # Assigns a foreign key to a state row; no status value changes.
    STATE_RELATION = "state_relation"
    # Status recorded on a result, report, metric or exception object.
    RESULT_RECORD = "result_record"
    # Direct write without verified lock and native validation; pending review.
    UNREVIEWED = "unreviewed"


class StateWriter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=rf"^{SCANNED_PACKAGE}/.+\.py$")
    fields: tuple[str, ...] = Field(min_length=1)
    machine: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: WriterKind
    # For native single writers: the only functions allowed to write the fields.
    functions: tuple[str, ...] = ()
    note: str = ""

    @model_validator(mode="after")
    def _fields_are_watched(self) -> StateWriter:
        unwatched = [field for field in self.fields if not WATCHED_FIELD.match(field)]
        if unwatched:
            raise ValueError(f"{self.path}: fields are not watched: {unwatched}")
        if (self.kind is WriterKind.NATIVE_SINGLE_WRITER) != bool(self.functions):
            raise ValueError(
                f"{self.path}: native single writers, and only they, name their functions"
            )
        return self


class StateMutationOwners(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    writers: tuple[StateWriter, ...]

    @model_validator(mode="after")
    def _each_write_has_one_owner(self) -> StateMutationOwners:
        seen: set[tuple[str, str]] = set()
        for writer in self.writers:
            for field in writer.fields:
                key = (writer.path, field)
                if key in seen:
                    raise ValueError(f"{writer.path}: field {field} is listed twice")
                seen.add(key)
        return self

    def owner_of(self) -> dict[tuple[str, str], StateWriter]:
        return {
            (writer.path, field): writer
            for writer in self.writers
            for field in writer.fields
        }


@dataclass(frozen=True, slots=True)
class StateWrite:
    path: str
    field: str
    line: int
    function: str = "<module>"


@dataclass(frozen=True, slots=True)
class OwnershipReport:
    unlisted: tuple[StateWrite, ...]
    stale: tuple[tuple[str, str], ...]
    shared_single_writer: tuple[str, ...]
    outside_writer_function: tuple[StateWrite, ...] = ()

    @property
    def clean(self) -> bool:
        return not (
            self.unlisted
            or self.stale
            or self.shared_single_writer
            or self.outside_writer_function
        )


def load_owners(path: Path = DEFAULT_OWNERS) -> StateMutationOwners:
    return StateMutationOwners.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def _target_fields(target: ast.expr) -> Iterator[str]:
    match target:
        case ast.Attribute(attr=attr):
            yield attr
        case ast.Tuple(elts=elements) | ast.List(elts=elements):
            for element in elements:
                yield from _target_fields(element)
        case _:
            return


def _written_fields(node: ast.AST) -> Iterator[str]:
    match node:
        case ast.Assign(targets=targets):
            for target in targets:
                yield from _target_fields(target)
        case ast.AnnAssign(target=target, value=value) if value is not None:
            yield from _target_fields(target)
        case ast.AugAssign(target=target):
            yield from _target_fields(target)
        case ast.Call(
            func=ast.Name(id="setattr"), args=[_, ast.Constant(value=str(name)), *_]
        ):
            yield name
        case ast.Call(func=ast.Attribute(attr="update"), keywords=keywords):
            yield from (keyword.arg for keyword in keywords if keyword.arg)
        case ast.Call(
            func=ast.Attribute(attr="bulk_update"),
            args=[_, ast.List(elts=names) | ast.Tuple(elts=names), *_],
        ):
            for name in names:
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    yield name.value
        case _:
            return


def scan_source(path: str, source: str) -> list[StateWrite]:
    """Return every watched field write in one module's source."""
    writes: list[StateWrite] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            function = node.name
        writes.extend(
            StateWrite(
                path=path,
                field=field,
                line=getattr(node, "lineno", 0),
                function=function,
            )
            for field in _written_fields(node)
            if WATCHED_FIELD.match(field)
        )
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source, filename=path), "<module>")
    return writes


def discover_state_writes(root: Path = PROJECT_ROOT) -> list[StateWrite]:
    writes: list[StateWrite] = []
    for module in sorted((root / SCANNED_PACKAGE).rglob("*.py")):
        if "migrations" in module.parts:
            continue
        relative = module.relative_to(root).as_posix()
        writes.extend(scan_source(relative, module.read_text(encoding="utf-8")))
    return writes


def compare_ownership(
    writes: list[StateWrite], owners: StateMutationOwners
) -> OwnershipReport:
    owner_of = owners.owner_of()
    discovered = {(write.path, write.field) for write in writes}
    unlisted = tuple(
        write for write in writes if (write.path, write.field) not in owner_of
    )
    stale = tuple(sorted(owner_of.keys() - discovered))
    outside = tuple(
        write
        for write in writes
        if (owner := owner_of.get((write.path, write.field))) is not None
        and owner.functions
        and write.function not in owner.functions
    )
    return OwnershipReport(
        unlisted=unlisted,
        stale=stale,
        shared_single_writer=_single_writer_conflicts(owner_of),
        outside_writer_function=outside,
    )


def _single_writer_conflicts(
    owner_of: Mapping[tuple[str, str], StateWriter],
) -> tuple[str, ...]:
    """A field with a native single writer may only share it with model methods."""
    by_field: defaultdict[tuple[str, str], list[StateWriter]] = defaultdict(list)
    for (_, field), writer in owner_of.items():
        by_field[(writer.machine, field)].append(writer)
    conflicts: list[str] = []
    for (machine, field), writers in sorted(by_field.items()):
        singles = [w.path for w in writers if w.kind is WriterKind.NATIVE_SINGLE_WRITER]
        others = [
            w.path
            for w in writers
            if w.kind
            not in {WriterKind.NATIVE_SINGLE_WRITER, WriterKind.MODEL_TRANSITION_METHOD}
        ]
        if singles and (len(singles) > 1 or others):
            conflicts.append(
                f"{machine}.{field}: native single writer {singles} shared with {others}"
            )
    return tuple(conflicts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that every status/state write has a reviewed owner."
    )
    parser.add_argument("--owners", type=Path, default=DEFAULT_OWNERS)
    args = parser.parse_args()

    owners = load_owners(args.owners)
    report = compare_ownership(discover_state_writes(), owners)
    for write in report.unlisted:
        print(
            f"{write.path}:{write.line}: unlisted write to '{write.field}'; "
            f"review it and add it to {DEFAULT_OWNERS.relative_to(PROJECT_ROOT)}"
        )
    for path, field in report.stale:
        print(f"{path}: listed write to '{field}' no longer exists; remove it")
    for problem in report.shared_single_writer:
        print(problem)
    for write in report.outside_writer_function:
        print(
            f"{write.path}:{write.line}: '{write.field}' written in "
            f"{write.function}, outside its native single writer functions"
        )
    if not report.clean:
        return 1
    counts: dict[WriterKind, int] = defaultdict(int)
    for writer in owners.writers:
        counts[writer.kind] += 1
    summary = ", ".join(f"{kind.value}={counts[kind]}" for kind in WriterKind)
    print(f"State mutation owners clean: {summary}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
