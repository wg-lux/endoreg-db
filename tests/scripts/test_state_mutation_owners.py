"""Every status/state write in endoreg_db has one reviewed owner."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.check_state_mutation_owners import (
    StateMutationOwners,
    StateWrite,
    compare_ownership,
    discover_state_writes,
    load_owners,
    scan_source,
)

pytestmark = pytest.mark.no_db

PATH = "endoreg_db/services/example.py"


def _owners(*writers: dict[str, object]) -> StateMutationOwners:
    return StateMutationOwners.model_validate({"version": 1, "writers": list(writers)})


def _writer(
    path: str = PATH,
    fields: tuple[str, ...] = ("status",),
    machine: str = "example",
    kind: str = "native_validated",
    functions: tuple[str, ...] | None = None,
) -> dict[str, object]:
    if functions is None:
        functions = ("set_status",) if kind == "native_single_writer" else ()
    return {
        "path": path,
        "fields": list(fields),
        "machine": machine,
        "kind": kind,
        "functions": list(functions),
    }


def test_repository_state_writers_match_reviewed_owners() -> None:
    report = compare_ownership(discover_state_writes(), load_owners())
    assert report.unlisted == ()
    assert report.stale == ()
    assert report.shared_single_writer == ()
    assert report.outside_writer_function == ()


@pytest.mark.parametrize(
    ("source", "field"),
    [
        ("job.status = 'done'", "status"),
        ("job.local_status = x", "local_status"),
        ("job.failure_class = ''", "failure_class"),
        ("video.state = state", "state"),
        ("job.retry_state += 1", "retry_state"),
        ("job.status: str = 'x'", "status"),
        ("a.status, b.other = 1, 2", "status"),
        ("setattr(job, 'cleanup_status', value)", "cleanup_status"),
        ("Job.objects.filter(pk=1).update(status='lost')", "status"),
        ("Job.objects.bulk_update(jobs, ['transfer_status'])", "transfer_status"),
    ],
)
def test_scan_detects_watched_writes(source: str, field: str) -> None:
    assert scan_source(PATH, source) == [StateWrite(path=PATH, field=field, line=1)]


@pytest.mark.parametrize(
    "source",
    [
        "job.statuses = []",
        "job.status_detail = ''",
        "status = 'local variable'",
        "Job.objects.create(status='queued')",
        "Job.objects.filter(status='queued')",
        "if job.status == 'done': pass",
        "setattr(job, name, value)",
    ],
)
def test_scan_ignores_reads_creation_and_unwatched_fields(source: str) -> None:
    assert scan_source(PATH, source) == []


@pytest.mark.parametrize(
    "writers",
    [
        [_writer(), _writer(machine="other")],
        [_writer(fields=("status_detail",))],
        [_writer(path="tests/example.py")],
        [_writer(kind="trusted")],
        [_writer(machine="Not-Snake")],
        [_writer(kind="native_single_writer", functions=())],
        [_writer(kind="native_validated", functions=("set_status",))],
    ],
)
def test_invalid_owner_maps_are_rejected(writers: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        _owners(*writers)


def test_unlisted_and_stale_writers_are_reported() -> None:
    write = StateWrite(path=PATH, field="status", line=3)
    stale_path = "endoreg_db/services/removed.py"
    report = compare_ownership([write], _owners(_writer(path=stale_path)))
    assert report.unlisted == (write,)
    assert report.stale == ((stale_path, "status"),)
    assert not report.clean


@pytest.mark.parametrize(
    ("other_kind", "clean"),
    [
        ("model_transition_method", True),
        ("native_validated", False),
        ("unreviewed", False),
        ("native_single_writer", False),
    ],
)
def test_native_single_writer_is_exclusive(other_kind: str, clean: bool) -> None:
    other_path = "endoreg_db/models/example.py"
    owners = _owners(
        _writer(kind="native_single_writer"),
        _writer(path=other_path, kind=other_kind),
    )
    writes = [
        StateWrite(path=PATH, field="status", line=1, function="set_status"),
        StateWrite(path=other_path, field="status", line=1),
    ]
    assert compare_ownership(writes, owners).clean is clean


@pytest.mark.parametrize(
    ("function", "clean"), [("set_status", True), ("helper", False)]
)
def test_native_single_writer_fields_stay_inside_writer_functions(
    function: str, clean: bool
) -> None:
    owners = _owners(_writer(kind="native_single_writer"))
    write = StateWrite(path=PATH, field="status", line=4, function=function)
    report = compare_ownership([write], owners)
    assert report.clean is clean
    assert report.outside_writer_function == (() if clean else (write,))


def test_scan_records_the_enclosing_function() -> None:
    source = "def set_status(job):\n    job.status = 'x'\n\nother.state = 1\n"
    assert scan_source(PATH, source) == [
        StateWrite(path=PATH, field="status", line=2, function="set_status"),
        StateWrite(path=PATH, field="state", line=4, function="<module>"),
    ]
