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

# PyO3's extension-module feature intentionally omits libpython from extension
# linkage. Rust test binaries and the stub generator embed Python, so link those
# two Cargo invocations explicitly while leaving the maturin wheel unchanged.
RUSTFLAGS="$python_embed_rustflags" \
  cargo test --manifest-path rust/endoreg_rust_backend/Cargo.toml
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

capability = ("report_source_snapshot", "report_source_snapshot_v1", "0.1.0")
assert capability in native.native_capabilities()
hls_capability = ("hls_state_machine", "hls_state_v1", "0.1.0")
assert hls_capability in native.native_capabilities()
assert (
    native.derive_hls_reservation_action("materializing", False, True, True)
    == "already_in_flight"
)
assert (
    native.derive_hls_publication_action("validated", True, True, True)
    == "defer"
)
assert native.derive_hls_reconciliation_action("queued", True) == "fail_and_cleanup"

root = Path(tempfile.mkdtemp(dir="."))
source = root / "source.pdf"
target = root / "snapshot.pdf"
payload = b"%PDF-1.4\nwheel-runtime-check\n%%EOF\n"
source.write_bytes(payload)
size_bytes, _modified_time_ns, digest = native.stable_snapshot_to_path(
    str(source),
    str(target),
    4096,
)
assert size_bytes == len(payload)
assert digest == hashlib.sha256(payload).hexdigest()
assert target.read_bytes() == payload
'
