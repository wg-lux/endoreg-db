"""Parser regressions and optional real MP-SPDZ v0.4.4 two-endpoint execution."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import os
import fcntl
import socket
import subprocess
import sys
from uuid import uuid4
from collections.abc import Iterator

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from endoreg_db.config.identity_hashing import identity_keyring_snapshot
from endoreg_db.services.patient_identity.matching.matching_backend import (
    BackendJob,
    BackendResult,
    MatchingBackendError,
    parse_backend_output,
    run_local_party,
)
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
)
from endoreg_db.utils.hashs import (
    encode_patient_identity,
    get_file_hash,
    get_identity_salt_fingerprint,
    get_patient_identity_fingerprint,
)
from endoreg_db.utils.paths import get_runtime_paths, resolve_runtime_path

pytestmark = pytest.mark.no_db
KEY = bytes(range(32)).hex().encode()
OTHER_KEY = bytes(range(32, 64)).hex().encode()
DOB = date(1980, 1, 2)


@pytest.mark.parametrize(
    "value,outcome,index",
    [
        (0, "no_match", None),
        (1, "single_match", 0),
        (2, "single_match", 1),
        (3, "ambiguous_matches", None),
        (4, "key_epoch_invalid", None),
        (5, "context_invalid", None),
    ],
)
def test_aggregate_output(value: int, outcome: str, index: int | None) -> None:
    result = parse_backend_output(
        f"LX_MATCH_RESULT={value}\n".encode(), party=1, candidate_count=2
    )
    assert (result.outcome, result.candidate_index) == (outcome, index)


@pytest.mark.parametrize(
    "output",
    [
        b"",
        b"LX_MATCH_RESULT=6",
        b"LX_MATCH_RESULT=01",
        b"LX_MATCH_RESULT=1\nLX_MATCH_RESULT=0",
        b"LX_MATCH_RESULT=1\xff",
        b"LX_MATCH_COMPLETE",
    ],
)
def test_malformed_output(output: bytes) -> None:
    with pytest.raises(MatchingBackendError):
        parse_backend_output(output, party=1, candidate_count=2)


def test_source_receives_completion_only() -> None:
    assert parse_backend_output(
        b"LX_MATCH_COMPLETE", party=0, candidate_count=2
    ) == BackendResult("complete")
    with pytest.raises(MatchingBackendError):
        parse_backend_output(b"LX_MATCH_RESULT=1", party=0, candidate_count=2)


@pytest.fixture(scope="module")
def native_jobs() -> Iterator[tuple[BackendJob, BackendJob]]:
    configured = os.environ.get("LX_MPSPDZ_ROOT")
    if not configured:
        pytest.skip(
            "Set LX_MPSPDZ_ROOT to the independently installed MP-SPDZ v0.4.4 tree"
        )
    installed = Path(configured)
    root = get_runtime_paths().runtime_root / f"matching-native-{uuid4().hex}"
    build = root / "build"
    ensure_directory(build)
    token = identity_keyring_snapshot.set(None)
    try:
        payload = encode_patient_identity("Ada", "Lovelace", DOB, "source-a")
        epoch = get_identity_salt_fingerprint(KEY)
        source = (
            Path(__file__).resolve().parents[2]
            / "scripts/reidentification/patient_matching_resolver.mpc"
        )
        atomic_copy_file(
            source=source,
            destination=build / "Programs/Source/patient_matching_resolver.mpc",
        )
        atomic_copy_file(
            source=installed / "Programs/Circuits/sha256.txt",
            destination=build / "Programs/Circuits/sha256.txt",
        )
        atomic_copy_file(
            source=installed / "compile.py", destination=build / "compile.py"
        )
        for file in (installed / "Compiler").rglob("*.py"):
            atomic_copy_file(
                source=file, destination=build / file.relative_to(installed)
            )
        args = [str(len(KEY)), f"{len(payload)},{len(payload)}", epoch]
        program = "patient_matching_resolver-" + "-".join(args)
        compiled = subprocess.run(
            [
                sys.executable,
                "compile.py",
                "-M",
                "-X",
                "patient_matching_resolver",
                *args,
            ],
            cwd=build,
            capture_output=True,
            timeout=180,
        )
        assert compiled.returncode == 0, compiled.stderr.decode()
        certs: list[tuple[bytes, bytes, str]] = []
        for party in (0, 1):
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"P{party}")])
            now = datetime.now(timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(
                    x509.BasicConstraints(ca=True, path_length=None), critical=True
                )
                .sign(key, hashes.SHA256())
            ).public_bytes(serialization.Encoding.PEM)
            private = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            name_hash = (
                subprocess.run(
                    ["openssl", "x509", "-subject_hash", "-noout"],
                    input=cert,
                    capture_output=True,
                    check=True,
                )
                .stdout.decode()
                .strip()
            )
            certs.append((cert, private, name_hash))
        jobs: list[BackendJob] = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        for party in (0, 1):
            runtime = root / f"party-{party}"
            atomic_copy_file(
                source=installed / "bin/Linux-adx/mascot-party.x",
                destination=runtime / "mascot-party.x",
                file_mode=0o700,
            )
            for file in (build / "Programs").rglob("*"):
                if file.is_file():
                    atomic_copy_file(
                        source=file, destination=runtime / file.relative_to(build)
                    )
            for other, (cert, private, name_hash) in enumerate(certs):
                for filename in (f"P{other}.pem", f"{name_hash}.0"):
                    atomic_write_file(
                        destination=runtime / "Player-Data" / filename, content=[cert]
                    )
                if party == other:
                    atomic_write_file(
                        destination=runtime / "Player-Data" / f"P{party}.key",
                        content=[private],
                        file_mode=0o600,
                    )
            pins = tuple(
                (str(f.relative_to(runtime)), get_file_hash(f))
                for f in runtime.rglob("*")
                if f.is_file() and f.suffix != ".key"
            )
            jobs.append(
                BackendJob(
                    party=0 if party == 0 else 1,
                    runtime_directory=str(
                        runtime.relative_to(get_runtime_paths().runtime_root)
                    ),
                    program=program,
                    key_size=len(KEY),
                    payload_sizes=(len(payload), len(payload)),
                    public_epoch=epoch,
                    peer_ip="127.0.0.1",
                    port=port,
                    artifacts=pins,
                    timeout_seconds=180,
                )
            )
        yield jobs[0], jobs[1]
    finally:
        identity_keyring_snapshot.reset(token)
        safe_rmtree(root)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("single", "single_match"),
        ("none", "no_match"),
        ("duplicate", "ambiguous_matches"),
        ("wrong_epoch", "key_epoch_invalid"),
        ("wrong_context", "context_invalid"),
    ],
)
def test_real_two_party_computation(
    native_jobs: tuple[BackendJob, BackendJob], case: str, expected: str
) -> None:
    token = identity_keyring_snapshot.set(None)
    try:
        correct = encode_patient_identity("Ada", "Lovelace", DOB, "source-a")
        wrong = encode_patient_identity("Eve", "Lovelace", DOB, "source-a")
        target = bytes.fromhex(
            get_patient_identity_fingerprint(
                "Ada", "Lovelace", DOB, "source-a", salt=KEY
            )
        )
    finally:
        identity_keyring_snapshot.reset(token)
    candidates = correct + wrong
    if case == "none":
        candidates = wrong + wrong
    if case == "duplicate":
        candidates = correct + correct
    key = OTHER_KEY if case == "wrong_epoch" else KEY
    with ThreadPoolExecutor(max_workers=2) as pool:
        source = pool.submit(run_local_party, native_jobs[0], key + target + bytes(32))
        receiver = pool.submit(
            run_local_party,
            native_jobs[1],
            (bytes([1]) * 32 if case == "wrong_context" else bytes(32)) + candidates,
        )
        a, b = source.result(), receiver.result()
    assert a == BackendResult("complete")
    assert b.outcome == expected
    assert b.candidate_index == (0 if case == "single" else None)


def test_real_missing_peer_is_not_nonmatch(
    native_jobs: tuple[BackendJob, BackendJob],
) -> None:
    with pytest.raises(MatchingBackendError, match="deadline|timed out"):
        run_local_party(replace(native_jobs[0], timeout_seconds=0.5), KEY + bytes(64))


def test_native_rejects_unpinned_trust_anchor(
    native_jobs: tuple[BackendJob, BackendJob],
) -> None:
    job = native_jobs[0]
    pins = tuple(
        (name, digest) for name, digest in job.artifacts if not name.endswith(".0")
    )
    with pytest.raises(MatchingBackendError, match="TLS trust"):
        run_local_party(replace(job, artifacts=pins), KEY + bytes(64))


def test_native_runtime_cannot_be_shared_concurrently(
    native_jobs: tuple[BackendJob, BackendJob],
) -> None:
    job = native_jobs[0]
    descriptor = os.open(
        resolve_runtime_path(job.runtime_directory), os.O_RDONLY | os.O_DIRECTORY
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MatchingBackendError, match="already in use"):
            run_local_party(job, KEY + bytes(64))
    finally:
        os.close(descriptor)
