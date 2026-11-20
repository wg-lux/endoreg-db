"""Frozen attacker: stdin-only bundle, no application imports or private state."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import platform
import resource
import sys
from collections import defaultdict
from collections.abc import Iterator
from datetime import date, timedelta
from time import perf_counter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Person(Model):
    person: str = ""
    first: str
    last: str
    dob: str

    def encoded(self) -> tuple[str, str, str]:
        return (
            self.first.strip(),
            self.last.strip(),
            date.fromisoformat(self.dob).isoformat(),
        )


class Row(Model):
    row: str
    digest: str
    centre: str
    generation: str = "active"
    scheme: str = "patient"


class Context(Model):
    centre: str
    generation: str = "active"
    key_id: str
    key: str = ""


class Known(Model):
    row: str
    person: str


class Bundle(Model):
    scenario: str
    mode: Literal["baseline", "key", "oracle"]
    rows: list[Row]
    contexts: list[Context]
    people: list[Person] = []
    names: list[tuple[str, str]] = []
    start: str = "1900-01-01"
    end: str = "2026-10-21"
    known: list[Known] = []
    scheme: Literal[
        "patient",
        "examiner",
        "examination",
        "artifact",
        "patient_local_uuid",
        "patient_hub_uuid",
    ] = "patient"
    hub_node_key: str = ""
    exam_times: list[str] = []
    patient_tokens: dict[str, str] = {}
    artifacts: list[str] = []
    artifact_people: dict[str, str] = {}
    max_evaluations: int = Field(default=250000, ge=0, le=1000000)
    max_seconds: float = Field(default=60, gt=0, le=60)
    oracle_limit: int = Field(default=32, ge=0, le=1000000)


class Match(Model):
    row: str
    encoded: tuple[str, str, str]
    people: list[str]


class Result(Model):
    scenario: str
    mode: str
    matches: list[Match]
    assertions: dict[str, list[str]]
    membership: dict[str, str]
    evaluations: int
    primitive_hmacs: int
    candidate_people: int
    unique_tuples: int
    reached_tuples: int
    cursor: int
    elapsed: float
    max_rss_kib: int
    stop: str
    unsupported_rows: int = 0
    isolation: dict[str, bool] = {}


def digest(key: str, parts: list[str]) -> str:
    return hmac.new(
        key.encode("utf-8"),
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def patient(key: str, encoded: tuple[str, str, str], centre: str) -> str:
    return digest(key, ["patient-identity-v1", *encoded, centre.strip()])


def uuid8(value: str) -> str:
    raw = bytearray.fromhex(value[:32])
    raw[6] = (raw[6] & 15) | 128
    raw[8] = (raw[8] & 63) | 128
    return str(UUID(bytes=bytes(raw)))


def patient_uuid(
    key: str, encoded: tuple[str, str, str], centre: str, hub: str = ""
) -> str:
    local = uuid8(
        digest(
            key,
            ["center-patient-uuid-v1", centre.strip(), patient(key, encoded, centre)],
        )
    )
    return (
        uuid8(digest(key, ["hub-record-pseudonym-v1", "patient", local, hub.strip()]))
        if hub
        else local
    )


def isolate() -> dict[str, bool]:
    """Landlock denies all new filesystem reads; no inherited files except pipes.

    Code, Python and dependencies are loaded before this point, before input is read.
    No rules grant access. Fail closed on unsupported kernels/architectures.
    """
    if platform.machine() != "x86_64":
        raise RuntimeError("Reviewed syscall numbers require Linux x86_64")
    libc = ctypes.CDLL(None, use_errno=True)
    # Landlock ABI 1: READ_FILE | READ_DIR | EXECUTE. No filesystem allow rules.
    mask = ctypes.c_uint64((1 << 2) | (1 << 3) | 1)
    fd = libc.syscall(444, ctypes.byref(mask), ctypes.sizeof(mask), 0)
    if fd < 0 or libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Landlock/no_new_privs unavailable")
    if libc.syscall(446, fd, 0) != 0:
        raise OSError(ctypes.get_errno(), "Landlock restriction failed")
    os.close(fd)
    denied: dict[str, bool] = {}
    for path in (
        __file__,
        "/etc/passwd",
        "/proc/self/environ",
        "/proc/1/root/etc/passwd",
    ):
        try:
            with open(path, "rb") as handle:
                handle.read(1)
        except PermissionError:
            denied[path] = True
        else:
            raise RuntimeError(f"Isolation probe readable: {path}")
    return denied


def attack(bundle: Bundle) -> Result:
    if bundle.scheme == "patient_hub_uuid" and not bundle.hub_node_key.strip():
        raise ValueError("Hub UUID attack requires a declared hub context")
    start = perf_counter()
    by_token: dict[str, list[Row]] = defaultdict(list)
    for row in bundle.rows:
        by_token[row.digest].append(row)
    assertions: dict[str, set[str]] = defaultdict(set)
    # Labels apply directly; equal tokens propagate all candidates, never select one.
    row_map = {row.row: row for row in bundle.rows}
    for known in bundle.known:
        for row in by_token[row_map[known.row].digest]:
            assertions[row.row].add(known.person)
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for person in bundle.people:
        if person.person:
            groups[person.encoded()].append(person.person)
        else:
            groups[person.encoded()]
    for artifact, person_id in bundle.artifact_people.items():
        groups[(artifact, "", "")].append(person_id)
    first_day, last_day = (
        date.fromisoformat(bundle.start),
        date.fromisoformat(bundle.end),
    )
    if last_day < first_day:
        raise ValueError("Reversed date interval")
    names = list(
        dict.fromkeys((first.strip(), last.strip()) for first, last in bundle.names)
    )
    days = (last_day - first_day).days + 1

    # Streaming: at most explicit population plus one date/name tuple in memory.
    def candidates() -> Iterator[tuple[str, str, str]]:
        if bundle.scheme == "artifact":
            for artifact in dict.fromkeys(bundle.artifacts):
                yield artifact, "", ""
        elif bundle.people:
            yield from groups
        else:
            for first, last in names:
                for offset in range(days):
                    yield first, last, (first_day + timedelta(days=offset)).isoformat()

    matches: list[Match] = []
    evaluations = primitives = cursor = reached = 0
    stop = "completed_domain"
    limit = bundle.oracle_limit if bundle.mode == "oracle" else bundle.max_evaluations
    unsupported = sum(r.scheme != bundle.scheme for r in bundle.rows)
    if not bundle.rows:
        stop = "no_comparison_targets"
    elif unsupported == len(bundle.rows) and bundle.mode != "baseline":
        stop = "unsupported_scheme"
    elif bundle.mode == "baseline" and bundle.scheme != "artifact":
        stop = "baseline_lookup_complete"
    else:
        for encoded in candidates():
            previous_evaluations = evaluations
            for context in bundle.contexts:
                times = bundle.exam_times if bundle.scheme == "examination" else [""]
                inner = ""
                for exam_time in times:
                    if (
                        evaluations >= limit
                        or perf_counter() - start >= bundle.max_seconds
                    ):
                        stop = "attempt_limit" if evaluations >= limit else "time_limit"
                        break
                    if bundle.scheme == "artifact":
                        token = hashlib.sha256(encoded[0].encode("utf-8")).hexdigest()
                    elif bundle.mode == "oracle":
                        print(
                            json.dumps(
                                {
                                    "query": [
                                        *encoded,
                                        context.centre,
                                        bundle.scheme,
                                        exam_time,
                                    ]
                                }
                            ),
                            flush=True,
                        )
                        token = sys.stdin.readline().strip()
                    elif bundle.scheme == "patient":
                        token = patient(context.key, encoded, context.centre)
                        primitives += 1
                    elif bundle.scheme in ("patient_local_uuid", "patient_hub_uuid"):
                        hub = (
                            bundle.hub_node_key
                            if bundle.scheme == "patient_hub_uuid"
                            else ""
                        )
                        token = patient_uuid(context.key, encoded, context.centre, hub)
                        primitives += 3 if hub else 2
                    elif bundle.scheme == "examiner":
                        token = digest(
                            context.key,
                            [
                                "examiner-identity-v1",
                                *encoded[:2],
                                context.centre.strip(),
                            ],
                        )
                        primitives += 1
                    else:
                        if not inner:
                            inner = bundle.patient_tokens.get(
                                json.dumps([*encoded, context.centre]), ""
                            )
                            if not inner:
                                inner = patient(context.key, encoded, context.centre)
                                primitives += 1
                        token = digest(
                            context.key,
                            [
                                "patient-examination-v1",
                                inner,
                                exam_time,
                                context.centre.strip(),
                            ],
                        )
                        primitives += 1
                    evaluations += 1
                    cursor += 1
                    for row in by_token.get(token, []):
                        if row.scheme != bundle.scheme:
                            continue
                        ids = sorted(set(groups.get(encoded, [])))
                        matches.append(Match(row=row.row, encoded=encoded, people=ids))
                        assertions[row.row].update(ids)
                if stop != "completed_domain":
                    break
            if evaluations > previous_evaluations:
                reached += 1
            if stop != "completed_domain":
                break
    # Explicit row mappings outrank ambiguous equality propagation for that row.
    direct: dict[str, set[str]] = defaultdict(set)
    for known in bundle.known:
        direct[known.row].add(known.person)
    assertions.update(direct)
    membership = {p.person: "unknown" for p in bundle.people if p.person}
    for ids in assertions.values():
        for person_id in ids:
            if membership.get(person_id) != "asserted_present":
                membership[person_id] = (
                    "asserted_present" if len(ids) == 1 else "ambiguous"
                )
    # Never infer absence from a guessed representation, incomplete dump or budget.
    return Result(
        scenario=bundle.scenario,
        mode=bundle.mode,
        matches=matches,
        assertions={r: sorted(ids) for r, ids in assertions.items()},
        membership=membership,
        evaluations=evaluations,
        primitive_hmacs=primitives,
        candidate_people=len(bundle.people),
        reached_tuples=reached,
        unique_tuples=len(set(bundle.artifacts))
        if bundle.scheme == "artifact"
        else len(groups)
        if bundle.people
        else len(names) * days,
        cursor=cursor,
        elapsed=perf_counter() - start,
        max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        stop=stop,
        unsupported_rows=unsupported,
    )


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (65, 65))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    checks = isolate()
    bundle = Bundle.model_validate_json(sys.stdin.readline())
    result = attack(bundle)
    result.isolation = checks
    print(result.model_dump_json(), flush=True)
