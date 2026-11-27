"""One-party MP-SPDZ worker boundary; provisioning and authorization are external.

The approved artifact pins must come from trusted enrollment, never the peer.
Each worker owns a separate protected runtime directory and only its own input.
The backend creates protocol state there; this adapter creates no input files.
"""

from __future__ import annotations

import ipaddress
import fcntl
import math
import os
import re
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

from django.views.decorators.debug import sensitive_variables

from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import resolve_runtime_path

Party = Literal[0, 1]
Outcome = Literal[
    "complete",
    "no_match",
    "single_match",
    "ambiguous_matches",
    "key_epoch_invalid",
    "context_invalid",
]
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-zA-Z0-9_,\-]{1,180}\Z")
_LIMIT = 65536


class MatchingBackendError(RuntimeError):
    """Sanitized failure: never includes private input or subprocess diagnostics."""


@dataclass(frozen=True)
class BackendResult:
    outcome: Outcome
    candidate_index: int | None = None


@dataclass(frozen=True)
class BackendJob:
    party: Party
    runtime_directory: str
    program: str
    key_size: int
    payload_sizes: tuple[int, ...]
    public_epoch: str
    peer_ip: str
    port: int
    # Runtime-relative file names within runtime_directory and enrolled SHA-256.
    artifacts: tuple[tuple[str, str], ...]
    timeout_seconds: float = 240.0

    def __post_init__(self) -> None:
        if type(self.party) is not int or self.party not in (0, 1):
            raise ValueError("Invalid backend party")
        if not _NAME.fullmatch(self.program) or not _HEX.fullmatch(self.public_epoch):
            raise ValueError("Invalid backend program or key epoch")
        if not 1 <= self.key_size <= 1024 or not 1 <= len(self.payload_sizes) <= 100:
            raise ValueError("Backend input dimensions exceed limits")
        if any(type(n) is not int or not 1 <= n <= 4096 for n in self.payload_sizes):
            raise ValueError("Invalid payload length")
        if not 1024 <= self.port <= 65534:
            raise ValueError("Invalid backend port")
        ipaddress.ip_address(self.peer_ip)
        if (
            not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 900
        ):
            raise ValueError("Invalid backend deadline")
        if len(dict(self.artifacts)) != len(self.artifacts):
            raise ValueError("Duplicate artifact pins")
        if any(not _HEX.fullmatch(digest) for _, digest in self.artifacts):
            raise ValueError("Invalid artifact digest")


def parse_backend_output(
    output: bytes, *, party: Party, candidate_count: int
) -> BackendResult:
    """Accept one aggregate marker only, after the caller checks successful exit."""
    if party not in (0, 1) or not 1 <= candidate_count <= 100 or len(output) > _LIMIT:
        raise MatchingBackendError("Invalid backend output envelope")
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise MatchingBackendError("Invalid backend output encoding") from None
    markers = [line for line in text.splitlines() if "LX_MATCH" in line]
    if party == 0:
        if markers != ["LX_MATCH_COMPLETE"]:
            raise MatchingBackendError("Unexpected source worker output")
        return BackendResult("complete")
    if len(markers) != 1 or not re.fullmatch(
        r"LX_MATCH_RESULT=(0|[1-9][0-9]{0,2})", markers[0]
    ):
        raise MatchingBackendError("Missing or malformed aggregate result")
    value = int(markers[0].split("=", 1)[1])
    if value == 0:
        return BackendResult("no_match")
    if 1 <= value <= candidate_count:
        return BackendResult("single_match", value - 1)
    if value == candidate_count + 1:
        return BackendResult("ambiguous_matches")
    if value == candidate_count + 2:
        return BackendResult("key_epoch_invalid")
    if value == candidate_count + 3:
        return BackendResult("context_invalid")
    raise MatchingBackendError("Aggregate result is outside its approved batch")


def _verify_artifacts(job: BackendJob) -> None:
    base = resolve_runtime_path(job.runtime_directory)
    pins = dict(job.artifacts)
    required = {
        "mascot-party.x",
        f"Programs/Schedules/{job.program}.sch",
        "Programs/Source/patient_matching_resolver.mpc",
        "Programs/Circuits/sha256.txt",
    }
    if not required.issubset(pins):
        raise MatchingBackendError("Missing enrolled backend artifacts")
    for name, digest in pins.items():
        path = resolve_runtime_path(f"{job.runtime_directory}/{name}")
        if not path.is_relative_to(base) or get_file_hash(path) != digest:
            raise MatchingBackendError("Backend artifact verification failed")
    schedule = resolve_runtime_path(
        f"{job.runtime_directory}/Programs/Schedules/{job.program}.sch"
    )
    lines = schedule.read_text(encoding="utf-8").splitlines()
    if len(lines) < 6 or not lines[1].isdigit():
        raise MatchingBackendError("Invalid backend schedule")
    tapes = lines[2].split()
    if len(tapes) != int(lines[1]) or not tapes:
        raise MatchingBackendError("Invalid backend tape inventory")
    for tape in tapes:
        name = tape.split(":", 1)[0]
        if "/" in name or "\\" in name or f"Programs/Bytecode/{name}.bc" not in pins:
            raise MatchingBackendError("Unpinned backend bytecode")
    arguments = (
        f"{job.key_size} {','.join(map(str, job.payload_sizes))} {job.public_epoch}"
    )
    if lines[5] != f"compile.py -M -X patient_matching_resolver {arguments}":
        raise MatchingBackendError(
            "Backend schedule does not match approved dimensions"
        )
    # Each worker must have only its own TLS private key, and both pinned public certificates.
    for party in (0, 1):
        cert = f"Player-Data/P{party}.pem"
        if cert not in pins:
            raise MatchingBackendError("Missing enrolled peer certificate")
    # OpenSSL verifies through hashed CApath entries, not the P*.pem names.
    anchors = [
        path
        for path in (base / "Player-Data").iterdir()
        if re.fullmatch(r"[0-9a-f]{8}\.[0-9]+", path.name)
    ]
    expected_certificates = {pins[f"Player-Data/P{party}.pem"] for party in (0, 1)}
    if (
        len(anchors) != 2
        or {pins.get(f"Player-Data/{path.name}") for path in anchors}
        != expected_certificates
    ):
        raise MatchingBackendError("Unpinned or incomplete TLS trust directory")
    own_key = resolve_runtime_path(
        f"{job.runtime_directory}/Player-Data/P{job.party}.key"
    )
    other_key = resolve_runtime_path(
        f"{job.runtime_directory}/Player-Data/P{1 - job.party}.key"
    )
    if not own_key.is_file() or own_key.stat().st_mode & 0o077 or other_key.exists():
        raise MatchingBackendError("Invalid local TLS key isolation")


@sensitive_variables()
def _exchange(
    command: list[str], *, directory: str, data: bytes, timeout: float
) -> bytes:
    """Bounded pipe exchange, deadline and process-group cleanup without secret files."""
    process = subprocess.Popen(
        command,
        cwd=directory,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
    )
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        assert process.stdin is not None and process.stdout is not None
        input_fd = process.stdin.fileno()
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = memoryview(data)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MatchingBackendError("Backend deadline exceeded")
                for event, _ in selector.select(min(remaining, 0.25)):
                    if event.fd == input_fd:
                        written = os.write(event.fd, pending[:4096])
                        pending = pending[written:]
                        if not pending:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(event.fd, 4096)
                        if not chunk:
                            selector.unregister(process.stdout)
                        output.extend(chunk)
                        if len(output) > _LIMIT:
                            raise MatchingBackendError("Backend output limit exceeded")
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if process.returncode != 0:
            raise MatchingBackendError("Backend computation failed")
        return bytes(output)
    except (OSError, subprocess.TimeoutExpired):
        raise MatchingBackendError("Backend transport failed or timed out") from None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


@sensitive_variables()
def run_local_party(job: BackendJob, private_input: bytes) -> BackendResult:
    """Execute one local role over mandatory TLS; return no partial result on failure."""
    expected = job.key_size + 64 if job.party == 0 else sum(job.payload_sizes) + 32
    if len(private_input) != expected:
        raise MatchingBackendError("Local input does not match approved dimensions")
    runtime_fd: int | None = None
    try:
        # MP-SPDZ may retain protocol state in its working directory. Serialize
        # cooperating workers there without creating a secret-bearing lock file.
        runtime_fd = os.open(
            resolve_runtime_path(job.runtime_directory), os.O_RDONLY | os.O_DIRECTORY
        )
        try:
            fcntl.flock(runtime_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MatchingBackendError(
                "Backend runtime directory is already in use"
            ) from None
        _verify_artifacts(job)
        directory = resolve_runtime_path(job.runtime_directory)
        executable = resolve_runtime_path(f"{job.runtime_directory}/mascot-party.x")
        command = [
            str(executable),
            str(job.party),
            job.program,
            "-N",
            "2",
            "-e",
            "-I",
            "-pn",
            str(job.port),
            "-h",
            job.peer_ip,
        ]
        output = _exchange(
            command,
            directory=str(directory),
            data=(" ".join(map(str, private_input)) + "\n").encode("ascii"),
            timeout=job.timeout_seconds,
        )
        _verify_artifacts(job)
        return parse_backend_output(
            output, party=job.party, candidate_count=len(job.payload_sizes)
        )
    except OSError:
        raise MatchingBackendError("Backend provisioning or execution failed") from None
    finally:
        if runtime_fd is not None:
            os.close(runtime_fd)
