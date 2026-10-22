"""Synthetic generator, isolated subprocess supervisor and private scorer."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import platform
import random
import secrets
import select
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import yaml

from scripts.reidentification.worker import (
    Bundle,
    Context,
    Known,
    Model,
    Person,
    Result,
    Row,
    digest,
    patient,
)


class Config(Model):
    schema_version: int
    reference_date: str
    birth_start: str
    max_evaluations: int
    max_seconds: float
    oracle_limit: int
    trials: int
    candidate_provenance: str
    names: list[tuple[str, str]]
    staff_names: list[tuple[str, str]]
    centres: list[str]
    partial_interval: tuple[str, str]
    examination_times: list[str]


class Truth(Model):
    people: list[Person]
    rows: dict[str, str]
    row_tuples: dict[str, tuple[str, str, str]]
    row_contexts: dict[str, tuple[str, str]]
    row_times: dict[str, str] = {}
    row_key_ids: dict[str, str] = {}


class Reproduction(Model):
    subject_seeds: list[int]
    keys: dict[str, str]
    population: list[Person]
    config_sha256: str


class Query(Model):
    query: tuple[str, str, str, str, str, str]


class Score(Model):
    trial: int
    scenario: str
    eligible_rows: int
    baseline_correct: int
    key_correct: int
    new_correct_associations: int
    false_associations: int
    ambiguous_rows: int
    digest_match_rows: int
    recovered_tuples: int
    membership_tp: int
    membership_fp: int
    membership_tn: int
    membership_fn: int
    membership_unknown: int
    membership_ambiguous: int
    baseline_membership_present: int
    new_membership_tp: int
    coverage: dict[str, int]
    evaluations: int
    primitive_hmacs: int
    elapsed: float
    max_rss_kib: int
    stop: str


def initialize(runtime: str) -> None:
    # Before any application import: discard inherited configuration and secrets.
    tool_path = os.environ.get("PATH", "")
    os.environ.clear()
    os.environ["PATH"] = tool_path
    os.environ["LX_RUNTIME_ROOT"] = runtime
    from django.conf import settings

    if settings.configured:
        raise RuntimeError("Fresh interpreter required")
    settings.configure(
        SECRET_KEY="isolated-synthetic-audit",
        INSTALLED_APPS=[],
        DATABASES={},
        USE_TZ=True,
        CELERY_TASK_ALWAYS_EAGER=True,
    )
    from endoreg_db.config.identity_hashing import identity_keyring_snapshot

    identity_keyring_snapshot.set(None)


def write(path: Path, value: str) -> None:
    from endoreg_db.utils.file_operations import atomic_write_file

    atomic_write_file(
        destination=path,
        content=[value.encode("utf-8")],
        file_mode=0o600,
        dir_mode=0o700,
    )


def checksum(path: Path) -> str:
    # Evidence checksums are ordinary source-byte SHA-256, not clinical file IDs.
    return hashlib.sha256(path.read_bytes()).hexdigest()


def conformance(key: str) -> list[dict[str, str]]:
    # TODO(evidence): endoreg_db/utils/hashs.py:1: a separate supplied hashs.py
    # attachment was unavailable; compare its checksum if one is later provided.
    # This run compares the checkout with the formulas in the task, not an unseen file.
    from django.core.exceptions import ImproperlyConfigured
    from endoreg_db.config.identity_hashing import identity_keyring_snapshot
    from endoreg_db.config.secret_keyring import SecretKeyring
    from endoreg_db.utils import hashs

    cases = [
        (" Zoë\t", 'O"Neil\\', date(1974, 2, 3), " centre-a "),
        (
            "Zoe\u0308",
            " Weiß ",
            datetime.fromisoformat("2001-03-04T23:59:59-07:00"),
            "centre-b",
        ),
    ]
    vectors: list[dict[str, str]] = []
    for first, last, dob, centre in cases:
        day = dob.date() if isinstance(dob, datetime) else dob
        encoded = (first.strip(), last.strip(), day.isoformat())
        actual = hashs.get_patient_identity_fingerprint(
            first, last, dob, centre, salt=key
        )
        assert actual == patient(key, encoded, centre)
        assert actual != patient(bytes.fromhex(key).decode("latin1"), encoded, centre)
        vectors.append(
            {
                "kind": "patient",
                "payload": hashs.encode_patient_identity(
                    first, last, dob, centre
                ).decode(),
                "digest": actual,
            }
        )
        assert hashs.get_examiner_hash(
            first, last, center_key=centre, salt=key
        ) == digest(key, ["examiner-identity-v1", *encoded[:2], centre.strip()])
        for exam in (
            None,
            date(2025, 1, 1),
            datetime.fromisoformat("2025-01-01T12:30:05.123456+02:00"),
            "2025-01-01T00:00:00",
            0,
        ):
            exam_text = (
                exam.isoformat()
                if isinstance(exam, (date, datetime))
                else str(exam or "")
            )
            outer = digest(
                key, ["patient-examination-v1", actual, exam_text, centre.strip()]
            )
            # Intentionally exercise runtime values beyond the helper's annotation.
            assert outer == hashs.get_patient_examination_hash(
                first,
                last,
                dob,
                center_key=centre,
                examination_date=cast(date | None, exam),
                salt=key,
            )
            vectors.append(
                {
                    "kind": "examination",
                    "patient_fp": actual,
                    "exam": exam_text,
                    "centre": centre.strip(),
                    "digest": outer,
                }
            )
    assert (
        hashs.get_identity_salt_fingerprint(key)
        == __import__("hmac")
        .new(key.encode(), b"endoreg-identity-salt-v1", hashlib.sha256)
        .hexdigest()
    )
    b64 = base64.b64encode(bytes.fromhex(key)).decode()
    assert hashs.get_patient_identity_fingerprint(
        "A", "B", date(2000, 1, 1), "a", salt=b64
    ) == patient(b64, ("A", "B", "2000-01-01"), "a")
    for invalid in ("short", b"\xff", " " + key):
        try:
            hashs.get_identity_hash(b"x", salt=invalid)
        except ImproperlyConfigured:
            pass
        else:
            raise AssertionError("invalid active key accepted")
    # Deliberately enrolled historical test generation; no weak active-key bypass.
    token = identity_keyring_snapshot.set(
        SecretKeyring(active=key.encode(), retiring=(b"historical-audit-only",))
    )
    try:
        assert (
            hashs.get_identity_hash(b"x", salt=b"historical-audit-only")
            == __import__("hmac")
            .new(b"historical-audit-only", b"x", hashlib.sha256)
            .hexdigest()
        )
    finally:
        identity_keyring_snapshot.reset(token)
    return vectors


def population(config: Config) -> list[Person]:
    # TODO(evidence): scripts/reidentification/config.yml:9: replace the artificial
    # candidate distribution only in a separately preregistered, authorized study;
    # synthetic overlap cannot estimate clinical-population reidentification rates.
    # Fixed auxiliary source, independent of all private subject seeds.
    rng = random.Random(902103)
    start = date.fromisoformat(config.birth_start)
    days = (date.fromisoformat(config.reference_date) - start).days + 1
    people = [
        Person(
            person=f"aux-{i:03}",
            first=first,
            last=last,
            dob=(start + timedelta(days=rng.randrange(days))).isoformat(),
        )
        for i, (first, last) in enumerate(config.names * 4)
    ]
    # Known ambiguous demographics, plus raw whitespace equivalence.
    people[1] = people[0].model_copy(update={"person": "aux-001"})
    people[2] = people[0].model_copy(
        update={"person": "aux-002", "first": "  " + people[0].first + "  "}
    )
    return people


def generate(
    config: Config,
    pop: list[Person],
    seed: int,
    keys: dict[str, str],
    architecture: Literal["shared", "independent"],
    scheme: str = "patient",
) -> tuple[list[Row], Truth]:
    from endoreg_db.utils import hashs

    rng = random.Random(seed)
    # Stratification is explicit: three duplicate people, 12 sampled auxiliary members,
    # 2 out-of-corpus subjects, one hidden twin of an absent auxiliary person.
    members = [
        *pop[:3],
        *rng.sample(pop[4:], 12),
        Person(person="hidden-one", first="Ilyana", last="Nadir", dob="1889-03-02"),
        Person(person="hidden-two", first="Qi", last="Zhen", dob="1997-08-09"),
        pop[3].model_copy(update={"person": "hidden-twin"}),
    ]
    rows: list[Row] = []
    truth = Truth(people=members, rows={}, row_tuples={}, row_contexts={})
    for person in members:
        for centre in config.centres[:2]:
            for generation in ("active", "retained"):
                key_id = "shared" if architecture == "shared" else centre
                if generation == "retained":
                    key_id = "retained"
                key = keys[key_id]
                for _ in range(2):
                    row_id = f"r-{rng.getrandbits(128):032x}"
                    exam_time = (
                        rng.choice(
                            (
                                date(2025, 1, 1),
                                date(2025, 1, 3),
                                datetime.fromisoformat(
                                    "2025-01-01T12:30:00.123456+02:00"
                                ),
                            )
                        )
                        if scheme == "examination"
                        else None
                    )
                    if scheme == "patient":
                        value = hashs.get_patient_identity_fingerprint(
                            person.first,
                            person.last,
                            date.fromisoformat(person.dob),
                            centre,
                            salt=key,
                        )
                    elif scheme == "examiner":
                        value = hashs.get_examiner_hash(
                            person.first, person.last, center_key=centre, salt=key
                        )
                    else:
                        value = hashs.get_patient_examination_hash(
                            person.first,
                            person.last,
                            date.fromisoformat(person.dob),
                            center_key=centre,
                            examination_date=exam_time,
                            salt=key,
                        )
                    rows.append(
                        Row(
                            row=row_id,
                            digest=value,
                            centre=centre,
                            generation=generation,
                            scheme=scheme,
                        )
                    )
                    truth.rows[row_id] = person.person
                    truth.row_tuples[row_id] = person.encoded()
                    truth.row_contexts[row_id] = (centre, generation)
                    truth.row_times[row_id] = exam_time.isoformat() if exam_time else ""
                    truth.row_key_ids[row_id] = key_id
    rng.shuffle(rows)
    return rows, truth


def execute(bundle: Bundle, oracle_keys: dict[str, str] | None = None) -> Result:
    worker = Path(__file__).with_name("worker.py").resolve()
    # exec, not forked Python state; no private file descriptors or environment.
    with subprocess.Popen(
        [sys.executable, "-I", str(worker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONHASHSEED": "0"},
        cwd="/",
        close_fds=True,
    ) as process:
        assert (
            process.stdin is not None
            and process.stdout is not None
            and process.stderr is not None
        )
        process.stdin.write(bundle.model_dump_json() + "\n")
        process.stdin.flush()
        calls = 0
        while True:
            if not select.select([process.stdout], [], [], 70)[0]:
                process.kill()
                raise TimeoutError("Attacker exceeded supervisor wall-clock limit")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(process.stderr.read())
            if '"query"' not in line:
                result = Result.model_validate_json(line)
                break
            query = Query.model_validate_json(line).query
            calls += 1
            if oracle_keys is None or calls > bundle.oracle_limit:
                raise RuntimeError("Unauthorized oracle call")
            first, last, dob, centre, scheme, exam = query
            if scheme != "patient" or exam or centre not in oracle_keys:
                raise ValueError("Oracle request outside declared contract")
            process.stdin.write(
                patient(oracle_keys[centre], (first, last, dob), centre) + "\n"
            )
            process.stdin.flush()
        process.stdin.close()
        if process.wait(timeout=70) != 0:
            raise RuntimeError(process.stderr.read())
        return result


def score(
    trial: int, bundle: Bundle, baseline: Result, result: Result, truth: Truth
) -> Score:
    def correct(r: Result) -> set[tuple[str, str]]:
        return {
            (row, ids[0])
            for row, ids in r.assertions.items()
            if len(ids) == 1 and truth.rows.get(row) == ids[0]
        }

    old, new = correct(baseline), correct(result)
    members = {p.person for p in truth.people}
    tp = fp = tn = fn = unknown = ambiguous = 0
    for person_id, state in result.membership.items():
        if state == "asserted_present":
            if person_id in members:
                tp += 1
            else:
                fp += 1
        elif state == "asserted_absent":
            if person_id in members:
                fn += 1
            else:
                tn += 1
        elif state == "ambiguous":
            ambiguous += 1
        else:
            unknown += 1
    coverage: dict[str, int] = {
        k: 0
        for k in (
            "outside_universe",
            "inside_beyond_prefix",
            "reached_matched",
            "reached_no_match",
            "wrong_key_context_or_scheme",
        )
    }
    matched = {m.row for m in result.matches}
    tuples = list(dict.fromkeys(p.encoded() for p in bundle.people))
    names = list(
        dict.fromkeys((first.strip(), last.strip()) for first, last in bundle.names)
    )
    days = (date.fromisoformat(bundle.end) - date.fromisoformat(bundle.start)).days + 1
    for row, encoded in truth.row_tuples.items():
        centre, generation = truth.row_contexts[row]
        compatible = any(
            c.centre == centre
            and c.generation == generation
            and c.key_id == truth.row_key_ids.get(row, c.key_id)
            for c in bundle.contexts
        )
        if row in matched:
            coverage["reached_matched"] += 1
        elif not compatible or result.stop == "unsupported_scheme":
            coverage["wrong_key_context_or_scheme"] += 1
        else:
            index: int | None = None
            if bundle.people and encoded in tuples:
                index = tuples.index(encoded)
            elif (
                not bundle.people
                and encoded[:2] in names
                and bundle.start <= encoded[2] <= bundle.end
            ):
                index = (
                    names.index(encoded[:2]) * days
                    + (
                        date.fromisoformat(encoded[2])
                        - date.fromisoformat(bundle.start)
                    ).days
                )
            if index is None or (
                bundle.scheme == "examination"
                and truth.row_times[row] not in bundle.exam_times
            ):
                coverage["outside_universe"] += 1
            else:
                context_index = next(
                    i
                    for i, c in enumerate(bundle.contexts)
                    if c.centre == centre and c.generation == generation
                )
                multiplier = (
                    len(bundle.exam_times) if bundle.scheme == "examination" else 1
                )
                ordinal = (index * len(bundle.contexts) + context_index) * multiplier
                coverage[
                    "reached_no_match"
                    if ordinal < result.evaluations
                    else "inside_beyond_prefix"
                ] += 1
    old_members = {
        p
        for p, s in baseline.membership.items()
        if s == "asserted_present" and p in members
    }
    return Score(
        trial=trial,
        scenario=bundle.scenario,
        eligible_rows=len(truth.rows),
        baseline_correct=len(old),
        key_correct=len(new),
        new_correct_associations=len(new - old),
        false_associations=sum(
            len(ids) == 1 and truth.rows.get(row) != ids[0]
            for row, ids in result.assertions.items()
        ),
        ambiguous_rows=sum(len(ids) > 1 for ids in result.assertions.values()),
        digest_match_rows=len(matched),
        recovered_tuples=len({m.encoded for m in result.matches}),
        membership_tp=tp,
        membership_fp=fp,
        membership_tn=tn,
        membership_fn=fn,
        membership_unknown=unknown,
        membership_ambiguous=ambiguous,
        baseline_membership_present=len(old_members),
        new_membership_tp=tp - len(old_members),
        coverage=coverage,
        evaluations=result.evaluations,
        primitive_hmacs=result.primitive_hmacs,
        elapsed=result.elapsed,
        max_rss_kib=result.max_rss_kib,
        stop=result.stop,
    )


def controls(public: Path, private: Path, key: str) -> None:
    from endoreg_db.utils import hashs

    centre = "centre-a"
    context = Context(centre=centre, key_id="shared", key=key)
    person = Person(
        person="control-disclosed", first="Synthetic", last="Person", dob="1980-07-14"
    )
    token = hashs.get_patient_identity_fingerprint(
        person.first, person.last, date.fromisoformat(person.dob), centre, salt=key
    )
    row = Row(row="labelled-control", digest=token, centre=centre)
    for name, people, names, start, end in (
        ("exact_tuple", [person], [], "1900-01-01", "2026-10-21"),
        (
            "known_name_known_year",
            [],
            [("Synthetic", "Person")],
            "1980-01-01",
            "1980-12-31",
        ),
    ):
        bundle = Bundle(
            scenario=name,
            mode="key",
            rows=[row],
            contexts=[context],
            people=people,
            names=names,
            start=start,
            end=end,
            known=[Known(row=row.row, person=person.person)],
        )
        baseline = execute(
            bundle.model_copy(
                update={
                    "mode": "baseline",
                    "contexts": [context.model_copy(update={"key": ""})],
                }
            )
        )
        result = execute(bundle)
        assert baseline.assertions == result.assertions
        assert result.evaluations == (1 if people else 366)
        write(
            public / (name + ".json"),
            json.dumps(
                {
                    "baseline": baseline.model_dump(),
                    "key": result.model_dump(),
                    "new_associations": 0,
                },
                indent=2,
            ),
        )
    # Independently chosen artifact corpus. Membership mappings are a separate input.
    corpus = ["artifact alpha\n", "artifact beta\n", "artifact gamma\n"]
    values: list[str] = []
    for i, content in enumerate(corpus):
        path = private / f"artifact-{i}.txt"
        write(path, content)
        actual = hashs.get_file_hash(path)
        assert actual == hashs.get_bytes_hash(content.encode("utf-8"))
        values.append(actual)
    for mapping in ({}, {corpus[0]: "artifact-owner-a"}):
        bundle = Bundle(
            scenario="artifact_mapping" if mapping else "artifact_unlabelled",
            mode="key",
            rows=[
                Row(row=f"artifact-row-{i}", digest=value, centre="", scheme="artifact")
                for i, value in enumerate(values[:2])
            ],
            contexts=[context],
            scheme="artifact",
            artifacts=corpus,
            artifact_people=mapping,
        )
        baseline = execute(
            bundle.model_copy(
                update={
                    "mode": "baseline",
                    "contexts": [context.model_copy(update={"key": ""})],
                }
            )
        )
        keyed = execute(bundle)
        assert (
            baseline.matches == keyed.matches
            and baseline.assertions == keyed.assertions
        )
        write(
            public / (bundle.scenario + ".json"),
            json.dumps(
                {
                    "manifest": bundle.model_dump(exclude={"contexts"}),
                    "baseline": baseline.model_dump(),
                    "keyed": keyed.model_dump(),
                    "incremental_key_gain": 0,
                },
                indent=2,
            ),
        )
    # TODO(evidence): endoreg_db/utils/hashs.py:191 and rust/.../file_identity.rs:110:
    # synthetic local plaintext was exercised; authenticated stored/encrypted-file
    # integration remains a distinct test lane, not evidence from this control.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    runtime: str = args.runtime
    initialize(runtime)
    from endoreg_db.utils.paths import get_runtime_paths
    from endoreg_db.utils.file_operations import ensure_directory

    config_path = Path(__file__).with_name("config.yml")
    config = Config.model_validate(yaml.safe_load(config_path.read_text()))
    root = get_runtime_paths().export_dir / "reidentification"
    ensure_directory(root, dir_mode=0o700)
    public = root / "public"
    private = root / "private"
    ensure_directory(private, dir_mode=0o700)
    if args.replay:
        reproduction = Reproduction.model_validate_json(args.replay.read_text())
        if reproduction.config_sha256 != checksum(config_path):
            raise ValueError("Frozen config differs")
    else:
        reproduction = Reproduction(
            subject_seeds=[secrets.randbits(128) for _ in range(config.trials)],
            keys={
                k: secrets.token_hex(32)
                for k in ("shared", "centre-a", "centre-b", "retained")
            },
            population=population(config),
            config_sha256=checksum(config_path),
        )
    write(private / "reproduction.json", reproduction.model_dump_json(indent=2))
    keys, pop = reproduction.keys, reproduction.population
    vectors = conformance(keys["shared"])
    write(
        public / "conformance.json", json.dumps(vectors, ensure_ascii=False, indent=2)
    )
    # Freeze code/config/corpus evidence before generating hidden records.
    source_paths = [
        "endoreg_db/utils/hashs.py",
        "endoreg_db/config/identity_hashing.py",
        "endoreg_db/config/secret_keyring.py",
        "endoreg_db/utils/verify_salt.py",
        "endoreg_db/utils/rust_backend.py",
        "endoreg_db/utils/storage_streaming.py",
        "endoreg_db/utils/file_operations.py",
        "endoreg_db/utils/paths.py",
        "endoreg_db/config/env.py",
        "rust/endoreg_rust_backend/src/file_identity.rs",
        "scripts/reidentification/worker.py",
        "scripts/reidentification/run.py",
        "scripts/reidentification/config.yml",
    ]
    revision = {
        "branch": subprocess.check_output(
            ["git", "branch", "--show-current"], text=True
        ).strip(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "dirty": subprocess.check_output(["git", "status", "--porcelain"], text=True),
        "checksums": {p: checksum(Path(p)) for p in source_paths},
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "dates_in_broad_domain": (
            date.fromisoformat(config.reference_date)
            - date.fromisoformat(config.birth_start)
        ).days
        + 1,
        "candidate_source": config.candidate_provenance,
        "key_generation_fingerprints": {
            key_id: __import__("hmac")
            .new(key.encode(), b"endoreg-identity-salt-v1", hashlib.sha256)
            .hexdigest()
            for key_id, key in keys.items()
        },
        "loaded_dependency_checksums": {
            name: checksum(Path(module_path))
            for name, module in sys.modules.copy().items()
            if name.startswith(("endoreg_db.", "endoreg_rust_backend"))
            and isinstance(module_path := getattr(module, "__file__", None), str)
            and Path(module_path).is_file()
        },
        "population_sha256": hashlib.sha256(
            json.dumps([p.model_dump() for p in pop], sort_keys=True).encode()
        ).hexdigest(),
    }
    write(public / "revision.json", json.dumps(revision, indent=2))
    controls(public, private, keys["shared"])
    scores: list[Score] = []
    for trial, seed in enumerate(reproduction.subject_seeds):
        shared, shared_truth = generate(config, pop, seed, keys, "shared")
        local, local_truth = generate(config, pop, seed, keys, "independent")
        contexts = [
            Context(centre=c, key_id="shared", key=keys["shared"])
            for c in config.centres[:2]
        ]
        base = Bundle(
            scenario="A",
            mode="key",
            rows=shared,
            contexts=contexts,
            names=config.names,
            start=config.birth_start,
            end=config.reference_date,
            max_evaluations=config.max_evaluations,
            max_seconds=config.max_seconds,
            oracle_limit=config.oracle_limit,
        )
        cases: list[tuple[Bundle, Truth]] = [(base, shared_truth)]
        cases.append(
            (
                base.model_copy(
                    update={
                        "scenario": "A_unknown_centre",
                        "contexts": [
                            *contexts,
                            Context(
                                centre="centre-c", key_id="shared", key=keys["shared"]
                            ),
                        ],
                        "rows": [
                            r.model_copy(update={"centre": "undisclosed"})
                            for r in shared
                        ],
                    }
                ),
                shared_truth,
            )
        )
        b = base.model_copy(update={"scenario": "B_shared", "people": pop, "names": []})
        cases.append((b, shared_truth))
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "B_shared_budget32",
                        "max_evaluations": config.oracle_limit,
                    }
                ),
                shared_truth,
            )
        )
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "B_independent",
                        "rows": local,
                        "contexts": [
                            Context(
                                centre="centre-a",
                                key_id="centre-a",
                                key=keys["centre-a"],
                            )
                        ],
                    }
                ),
                local_truth,
            )
        )
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "B_retained_exposed",
                        "contexts": [
                            Context(
                                centre=c,
                                key_id="retained",
                                key=keys["retained"],
                                generation="retained",
                            )
                            for c in config.centres[:2]
                        ],
                    }
                ),
                shared_truth,
            )
        )
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "B_wrong_key",
                        "contexts": [
                            Context(centre=c, key_id="centre-b", key=keys["centre-b"])
                            for c in config.centres[:2]
                        ],
                    }
                ),
                shared_truth,
            )
        )
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "legacy_unsupported",
                        "rows": [
                            r.model_copy(update={"scheme": "legacy-not-evaluated"})
                            for r in shared
                        ],
                    }
                ),
                shared_truth,
            )
        )
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "B_oracle",
                        "mode": "oracle",
                        "contexts": [
                            c.model_copy(update={"key": ""}) for c in contexts
                        ],
                    }
                ),
                shared_truth,
            )
        )
        cases.append(
            (
                base.model_copy(
                    update={
                        "scenario": "C_independent_names_narrow_dates",
                        "names": config.names[:4],
                        "start": config.partial_interval[0],
                        "end": config.partial_interval[1],
                    }
                ),
                shared_truth,
            )
        )
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "D_labelled",
                        "known": [
                            Known(row=r.row, person=shared_truth.rows[r.row])
                            for r in shared
                        ],
                    }
                ),
                shared_truth,
            )
        )
        cases.append(
            (
                b.model_copy(
                    update={"scenario": "B_single_target", "rows": shared[:1]}
                ),
                Truth(
                    people=shared_truth.people,
                    rows={r.row: shared_truth.rows[r.row] for r in shared[:1]},
                    row_tuples={
                        r.row: shared_truth.row_tuples[r.row] for r in shared[:1]
                    },
                    row_contexts={
                        r.row: shared_truth.row_contexts[r.row] for r in shared[:1]
                    },
                ),
            )
        )
        cases.append(
            (
                b.model_copy(update={"scenario": "key_without_dump", "rows": []}),
                Truth(people=[], rows={}, row_tuples={}, row_contexts={}),
            )
        )
        exams, exam_truth = generate(config, pop, seed, keys, "shared", "examination")
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "examination_independent_times",
                        "scheme": "examination",
                        "exam_times": config.examination_times,
                        "rows": exams,
                    }
                ),
                exam_truth,
            )
        )
        # This separately declares identity-to-patient-token pairs as existing disclosure.
        # Only novel examination-row associations may gain credit in this condition.
        patient_tokens = {
            json.dumps([*p.encoded(), c.centre]): patient(c.key, p.encoded(), c.centre)
            for p in pop
            for c in contexts
        }
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "examination_patient_tokens_exposed",
                        "scheme": "examination",
                        "exam_times": config.examination_times,
                        "rows": exams,
                        "patient_tokens": patient_tokens,
                    }
                ),
                exam_truth,
            )
        )
        # Staff candidates are a separately declared source; use independent synthetic sampling.
        staff = [
            Person(person=f"staff-{i}", first=first, last=last, dob="2000-01-01")
            for i, (first, last) in enumerate(config.staff_names * 6)
        ]
        staff_rows, staff_truth = generate(
            config, staff, seed, keys, "shared", "examiner"
        )
        cases.append(
            (
                b.model_copy(
                    update={
                        "scenario": "examiner",
                        "scheme": "examiner",
                        "rows": staff_rows,
                        "people": staff,
                    }
                ),
                staff_truth,
            )
        )
        for bundle, truth in cases:
            name = f"{trial}-{bundle.scenario}"
            write(private / (name + "-truth.json"), truth.model_dump_json(indent=2))
            write(private / (name + "-attacker.json"), bundle.model_dump_json(indent=2))
            baseline_bundle = bundle.model_copy(
                update={
                    "mode": "baseline",
                    "contexts": [
                        c.model_copy(update={"key": ""}) for c in bundle.contexts
                    ],
                }
            )
            baseline = execute(baseline_bundle)
            result = execute(
                bundle,
                {c: keys["shared"] for c in config.centres[:2]}
                if bundle.mode == "oracle"
                else None,
            )
            write(
                public / (name + "-baseline.json"), baseline.model_dump_json(indent=2)
            )
            write(public / (name + "-result.json"), result.model_dump_json(indent=2))
            manifest = bundle.model_dump(exclude={"contexts"})
            manifest["contexts"] = [
                c.model_dump(exclude={"key"}) for c in bundle.contexts
            ]
            manifest["key_encoding"] = (
                "CSPRNG 32 bytes rendered hex; literal UTF-8 of 64 characters; no hex decoding"
            )
            manifest["key_provenance"] = (
                "Independent secrets.token_hex; restricted attacker input; no production enrollment"
            )
            manifest["observations"] = (
                "Rows shuffled; centre/generation/scheme explicit dump metadata except A_unknown_centre; independently declared centre list"
            )
            manifest["prior_disclosures"] = (
                "D: every row label supplied; other cases: no membership or row labels; candidate identities independent"
            )
            manifest["permitted_computation"] = (
                "Offline evaluations and equality lookup; oracle case 32 requests only; no scorer feedback; no absence assertions"
            )
            manifest["identity_assertion_assumption"] = (
                "Unique auxiliary candidate per matching tuple is a conditional person assertion, not proof of uniqueness in the world"
            )
            manifest["corpus_completeness"] = (
                "Synthetic dump scope only; possible alternate stored demographic representations unknown; no asserted absences"
            )
            manifest["baseline_policy"] = (
                "Direct known row labels, equality propagation retaining ambiguity; artifact corpus SHA-256 lookup without HMAC key; no undeclared oracle"
            )
            manifest["search_policy"] = (
                "Fixed source order, ascending date within name, centre order, then declared examination-time order; never stop at first hit"
            )
            manifest["examination_time_provenance"] = (
                "Independent synthetic four-value calendar; no target time obtained from truth"
                if bundle.scheme == "examination"
                else "not applicable"
            )
            manifest["patient_token_provenance"] = (
                "Explicit additional candidate-to-token auxiliary mapping, including nonmembers; no target membership supplied"
                if bundle.patient_tokens
                else "none"
            )
            write(
                public / (name + "-knowledge.json"),
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            scores.append(score(trial, bundle, baseline, result, truth))
            print(name, result.evaluations, result.stop, flush=True)
    write(
        public / "scores.json", json.dumps([s.model_dump() for s in scores], indent=2)
    )
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(Score.model_fields))
    writer.writeheader()
    writer.writerows(s.model_dump() for s in scores)
    write(public / "scores.csv", output.getvalue())
    print(root)


if __name__ == "__main__":
    main()
