#!/usr/bin/env bash
# Run the native crate's Rust unit tests; extra arguments are passed to cargo test.
#
# PyO3's extension-module feature intentionally omits libpython from extension
# linkage. Test binaries embed Python, so link libpython explicitly here while
# leaving the maturin wheel build unchanged.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

rustflags=${RUSTFLAGS:-}
for linker_flag in $(python3-config --embed --ldflags); do
  rustflags+=" -C link-arg=$linker_flag"
done

RUSTFLAGS="$rustflags" \
  cargo test --manifest-path rust/endoreg_rust_backend/Cargo.toml "$@"
