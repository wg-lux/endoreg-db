"""Independent vectors and bounded search contracts for the standalone tool."""

import hashlib
import hmac
import json

import pytest

from scripts import benchmark_sha256 as benchmark

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("algorithm", benchmark.ALGORITHMS)
@pytest.mark.parametrize("plaintext", ["", "ba", "ä"])
def test_recovery(algorithm: str, plaintext: str) -> None:
    salt = "" if algorithm == "sha256" else "known-ä"
    payload = ("[" + plaintext + "]").encode()
    key = salt.encode()
    expected = {
        "hmac-sha256": hmac.digest(key, payload, "sha256"),
        "sha256": hashlib.sha256(payload).digest(),
        "salt-prefix": hashlib.sha256(key + payload).digest(),
        "salt-suffix": hashlib.sha256(payload + key).digest(),
    }[algorithm]
    result = benchmark.search(
        expected.hex().upper(),
        benchmark.make_digest(algorithm, salt),
        charset="abä",
        min_length=0,
        max_length=2,
        prefix="[",
        suffix="]",
    )
    assert result.status == "found"
    assert result.candidate == plaintext
    assert result.search_space == 13
    assert result.elapsed_seconds >= 0


def test_hmac_rfc4231_vector() -> None:
    assert (
        benchmark.make_digest("hmac-sha256", "Jefe")(
            "what do ya want for nothing?"
        ).hex()
        == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
    )


@pytest.mark.parametrize(
    "limit,status,attempts", [(1, "attempt_limit", 1), (2, "exhausted", 2)]
)
def test_limits_and_exhaustion(limit: int, status: str, attempts: int) -> None:
    digest = benchmark.make_digest("sha256", "")
    result = benchmark.search(
        digest("z").hex(),
        digest,
        charset="ab",
        min_length=1,
        max_length=1,
        max_attempts=limit,
    )
    assert (result.status, result.attempts, result.candidate) == (
        status,
        attempts,
        None,
    )


def test_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([0.0, 1.0, 1.0])
    monkeypatch.setattr(benchmark, "perf_counter", lambda: next(ticks))
    result = benchmark.search(
        "0" * 64,
        benchmark.make_digest("sha256", ""),
        charset="a",
        min_length=1,
        max_length=1,
        max_seconds=0.5,
    )
    assert (result.status, result.attempts) == ("time_limit", 0)
    assert result.estimated_full_search_seconds is None


def test_interrupt() -> None:
    def interrupted(_: str) -> bytes:
        raise KeyboardInterrupt

    result = benchmark.search(
        "0" * 64, interrupted, charset="a", min_length=1, max_length=1
    )
    assert (result.status, result.attempts) == ("interrupted", 0)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--target", "not-a-hash"],
        ["--plaintext", "a", "--charset", "aa"],
        ["--plaintext", "a", "--charset", ""],
        ["--plaintext", "a", "--min-length", "-1"],
        ["--plaintext", "a", "--min-length", "7"],
        ["--plaintext", "a", "--max-length", "257"],
        ["--plaintext", "a", "--max-seconds", "nan"],
        ["--plaintext", "a", "--max-seconds", "inf"],
        ["--plaintext", "a", "--max-seconds", "0"],
        ["--plaintext", "a", "--max-attempts", "0"],
        ["--plaintext", "a", "--algorithm", "sha256"],
    ],
)
def test_invalid_cli(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        benchmark.main(["--salt", "test", *arguments])
    assert error.value.code == 2


def test_historical_json_cli(capsys: pytest.CaptureFixture[str]) -> None:
    prefix = '["patient-identity-v1","Synthetic","Person","'
    suffix = '-01-02",1]'
    payload = json.dumps(
        ["patient-identity-v1", "Synthetic", "Person", "1980-01-02", 1],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    target = hmac.digest(b"benchmark-only", payload.encode(), "sha256").hex()
    assert (
        benchmark.main(
            [
                "--salt",
                "benchmark-only",
                "--target",
                target,
                "--prefix",
                prefix,
                "--suffix=" + suffix,
                "--min-length",
                "4",
                "--max-length",
                "4",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["candidate"] == "1980"
    assert output["attempts"] == 1981


def test_plaintext_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert benchmark.main(["--salt", "test", "--plaintext", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["attempts"] == 1


def test_unknown_algorithm() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        benchmark.make_digest("unknown", "test")
