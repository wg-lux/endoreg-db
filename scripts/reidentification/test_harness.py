"""Pure contracts plus a real denied-filesystem attacker subprocess."""

from __future__ import annotations

import pytest
import subprocess
import sys

from scripts.reidentification.run import Truth, execute, score
from scripts.reidentification.worker import (
    Bundle,
    Context,
    Known,
    Person,
    Row,
    attack,
    patient,
)

KEY = "1b8d6c2e7a9f04356d8c1e7b2a4f60939c5d8a0e2f7b14366a9c8e1d5b307f42"


def fixture_bundle() -> Bundle:
    p = Person(person="independent-person", first=" Éva ", last="X", dob="1972-03-04")
    value = patient(KEY, p.encoded(), "a")
    return Bundle(
        scenario="test",
        mode="key",
        people=[p],
        contexts=[Context(centre="a", key_id="test", key=KEY)],
        rows=[
            Row(row="opaque-1", digest=value, centre="a"),
            Row(row="opaque-2", digest=value, centre="a"),
        ],
    )


def test_isolated_worker_denies_files_and_has_same_results() -> None:
    bundle = fixture_bundle()
    isolated = execute(bundle)
    assert isolated.assertions == attack(bundle).assertions
    assert len(isolated.isolation) == 4 and all(isolated.isolation.values())


@pytest.mark.parametrize("budget", [0, 1])
def test_budget_and_reuse(budget: int) -> None:
    bundle = fixture_bundle().model_copy(update={"max_evaluations": budget})
    result = attack(bundle)
    assert result.evaluations == budget
    assert result.primitive_hmacs == budget
    assert len(result.matches) == 2 * budget
    assert result.membership["independent-person"] == (
        "asserted_present" if budget else "unknown"
    )


def test_time_budget() -> None:
    result = attack(fixture_bundle().model_copy(update={"max_seconds": 1e-12}))
    assert result.stop == "time_limit" and result.evaluations == 0


def test_ambiguity_and_whitespace_deduplication() -> None:
    bundle = fixture_bundle()
    bundle.people.append(
        bundle.people[0].model_copy(
            update={"person": "different-person", "first": "Éva"}
        )
    )
    result = attack(bundle)
    assert result.evaluations == 1 and result.unique_tuples == 1
    assert set(result.membership.values()) == {"ambiguous"}
    assert result.assertions["opaque-1"] == ["different-person", "independent-person"]


def test_baseline_propagates_known_tokens_and_labelled_gain_is_zero() -> None:
    bundle = fixture_bundle()
    bundle.known = [Known(row="opaque-1", person="independent-person")]
    baseline = attack(bundle.model_copy(update={"mode": "baseline"}))
    keyed = attack(bundle)
    assert baseline.assertions == keyed.assertions
    truth = Truth(
        people=bundle.people,
        rows={r.row: "independent-person" for r in bundle.rows},
        row_tuples={r.row: bundle.people[0].encoded() for r in bundle.rows},
        row_contexts={r.row: ("a", "active") for r in bundle.rows},
    )
    scored = score(0, bundle, baseline, keyed, truth)
    assert scored.new_correct_associations == 0 and scored.baseline_correct == 2


def test_scorer_does_not_invent_person_from_tuple() -> None:
    bundle = fixture_bundle()
    truth = Truth(
        people=bundle.people,
        rows={r.row: "private-person" for r in bundle.rows},
        row_tuples={r.row: bundle.people[0].encoded() for r in bundle.rows},
        row_contexts={r.row: ("a", "active") for r in bundle.rows},
    )
    bundle.people = [bundle.people[0].model_copy(update={"person": ""})]
    result = attack(bundle)
    scored = score(
        0, bundle, attack(bundle.model_copy(update={"mode": "baseline"})), result, truth
    )
    assert scored.recovered_tuples == 1 and scored.new_correct_associations == 0


def test_absent_twin_is_false_attribution() -> None:
    bundle = fixture_bundle()
    truth = Truth(
        people=[bundle.people[0].model_copy(update={"person": "hidden-twin"})],
        rows={r.row: "hidden-twin" for r in bundle.rows},
        row_tuples={r.row: bundle.people[0].encoded() for r in bundle.rows},
        row_contexts={r.row: ("a", "active") for r in bundle.rows},
    )
    scored = score(
        0,
        bundle,
        attack(bundle.model_copy(update={"mode": "baseline"})),
        attack(bundle),
        truth,
    )
    assert scored.membership_fp == 1 and scored.false_associations == 2
    assert scored.new_correct_associations == 0


@pytest.mark.parametrize("centre,key", [("other", KEY), ("a", KEY[::-1])])
def test_wrong_context_abstains(centre: str, key: str) -> None:
    bundle = fixture_bundle()
    bundle.contexts = [Context(centre=centre, key_id="wrong", key=key)]
    result = attack(bundle)
    assert not result.matches and set(result.membership.values()) == {"unknown"}


def test_no_dump_no_observation() -> None:
    bundle = fixture_bundle().model_copy(update={"rows": []})
    assert not attack(bundle).matches


def test_validation_rejects_private_fields_and_invalid_budget() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Bundle.model_validate(
            {**fixture_bundle().model_dump(), "ground_truth": {"person": "x"}}
        )
    with pytest.raises(ValidationError):
        Bundle.model_validate(
            {**fixture_bundle().model_dump(), "max_evaluations": 1000001}
        )


def test_production_conformance_without_real_settings_or_keyring() -> None:
    code = (
        "from scripts.reidentification.run import initialize, conformance; "
        "initialize('/tmp/reidentification-conformance-unused'); "
        "from django.conf import settings; "
        "assert settings.DATABASES == {}; "
        f"assert len(conformance('{KEY}')) == 12"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={
            "DJANGO_SETTINGS_MODULE": "must_not_import_real_settings",
            "DJANGO_SALT_FILE": "/must-not-read-a-keyring",
        },
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_artifact_bytes_and_key_independence() -> None:
    import hashlib

    content = " whitespace matters\n"
    bundle = Bundle(
        scenario="artifact",
        mode="baseline",
        scheme="artifact",
        artifacts=[content],
        contexts=[Context(centre="", key_id="none")],
        rows=[
            Row(
                row="artifact",
                digest=hashlib.sha256(content.encode()).hexdigest(),
                centre="",
                scheme="artifact",
            )
        ],
    )
    result = attack(bundle)
    assert len(result.matches) == 1 and result.primitive_hmacs == 0
    assert not result.assertions["artifact"]
    assert attack(bundle.model_copy(update={"mode": "key"})).matches == result.matches


def test_unsupported_scheme_is_not_domain_exhaustion() -> None:
    bundle = fixture_bundle()
    bundle.rows = [r.model_copy(update={"scheme": "legacy"}) for r in bundle.rows]
    result = attack(bundle)
    assert result.stop == "unsupported_scheme" and result.evaluations == 0


def test_equal_oracle_and_key_budgets() -> None:
    bundle = fixture_bundle()
    bundle.oracle_limit = 1
    bundle.max_evaluations = 1
    oracle = execute(
        bundle.model_copy(
            update={
                "mode": "oracle",
                "contexts": [c.model_copy(update={"key": ""}) for c in bundle.contexts],
            }
        ),
        {"a": KEY},
    )
    keyed = execute(bundle)
    assert oracle.evaluations == keyed.evaluations == 1
    assert oracle.matches == keyed.matches
