#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runtime_check_dir=$(mktemp -d)
generated_stub="$repo_root/rust/endoreg_rust_backend/endoreg_rust_backend.pyi"
expected_stub="$runtime_check_dir/endoreg_rust_backend.pyi"

cleanup() {
  rm -f -- "$generated_stub"
  rm -rf -- "$runtime_check_dir"
}
trap cleanup EXIT

cd "$repo_root"
python_embed_rustflags=${RUSTFLAGS:-}
for linker_flag in $(python3-config --embed --ldflags); do
  python_embed_rustflags+=" -C link-arg=$linker_flag"
done

scripts/run_rust_tests.sh
# The stub generator embeds Python like the test binaries; see run_rust_tests.sh.
RUSTFLAGS="$python_embed_rustflags" \
  cargo run --manifest-path rust/endoreg_rust_backend/Cargo.toml --bin stub_gen
cp endoreg_db/endoreg_rust_backend.pyi "$expected_stub"
ruff format "$generated_stub"
ruff format "$expected_stub"
cmp "$generated_stub" "$expected_stub"

mkdir -p "$runtime_check_dir/wheels"
maturin build --release --out "$runtime_check_dir/wheels"
wheel_paths=("$runtime_check_dir"/wheels/*.whl)
test "${#wheel_paths[@]}" -eq 1

uv venv "$runtime_check_dir/venv"
uv pip install \
  --python "$runtime_check_dir/venv/bin/python" \
  --no-deps \
  "${wheel_paths[0]}"

cd "$runtime_check_dir"
"$runtime_check_dir/venv/bin/python" -c '
import hashlib
import tempfile
from pathlib import Path

import endoreg_db.endoreg_rust_backend as native

# Contracts are versioned independently of the crate implementation version.
contracts = {(name, contract) for name, contract, _version in native.native_capabilities()}
for required in (
    ("report_source_snapshot", "report_source_snapshot_v1"),
    ("media_decisions", "media_decisions_v1"),
    ("hls_state_machine", "hls_state_v1"),
    ("lifecycle_state_machine", "lifecycle_state_v3"),
    ("status_transition", "status_transition_v2"),
    ("state_vocabulary", "state_vocabulary_v1"),
    ("hub_transfer_state_machine", "hub_transfer_state_v2"),
    ("hls_artifact_transition", "hls_artifact_transition_v1"),
):
    assert required in contracts, required
vocabularies = dict(native.native_state_vocabularies())
assert native.media_validation_available(False, "lost") is False
assert native.segment_status_with_history(True, True, True, "failure") == "cleanup_failed"
assert native.hub_export_integrity(True, True, "a", "b", False, None) == "hash_metadata_mismatch"
assert "validation_comment" in native.direct_identifier_fields()
assert native.decide_status_transition("upload_job", "pending", "processing") is True
assert native.decide_status_transition("upload_job_lease_recovery", "processing", "error") is True
assert "materializing" in vocabularies["hls_in_flight_status"]
assert "transient_retry" in vocabularies["hub_failure_class"]
assert native.reduce_hub_transfer_event("completed", None, "fail", None, "transient_retry") == ("completed", None, False)
assert native.decide_hls_artifact_transition("validated", "ready") is True
assert native.decide_status_transition("upload_job_source_cleanup", "completed", "skipped") is False
assert native.reduce_hub_transfer_event("failed", "integrity_inconsistency", "retry") == ("failed", "integrity_inconsistency", False)
assert native.transition_operation_lifecycle("queued", "claim") == "claimed"
assert (
    native.derive_hls_reservation_action("materializing", False, True, True)
    == "already_in_flight"
)
assert (
    native.derive_hls_publication_action("validated", True, True, True)
    == "defer"
)
assert native.derive_hls_reconciliation_action("queued", True) == "fail_and_cleanup"

# Report snapshots copy atomically in Python and hash through this native identity.
root = Path(tempfile.mkdtemp(dir="."))
source = root / "source.pdf"
payload = b"%PDF-1.4\nwheel-runtime-check\n%%EOF\n"
source.write_bytes(payload)
size_bytes, _modified_time_ns, digest = native.stable_file_identity(source, 4096)
assert size_bytes == len(payload)
assert digest == hashlib.sha256(payload).hexdigest()
'
