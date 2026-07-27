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
cargo test --manifest-path rust/endoreg_rust_backend/Cargo.toml
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
