# Handoff: `lx-report-generator`

## Purpose
This handoff describes how to:
- install the Rust PDF renderer so `endoreg_db` can use it at runtime
- publish the renderer as a Cargo crate (`crates.io`)

Renderer source lives at:
- `lx-report-generator/`

Backend integration entry point:
- `endoreg_db/services/report_pdf_renderer.py`

Backend runtime env var:
- `ENDOREG_REPORT_PDF_RENDERER_BIN`

## 1. Install For `endoreg_db` Runtime (Repo Root Workflow)

### Recommended local/dev: use the standalone module directly
From `/home/admin/endoreg-db`:

```bash
cd lx-report-generator
direnv allow   # optional
devenv shell
```

This bootstraps the release binary automatically on shell entry if missing.

Then export the runtime path for `endoreg_db`:

```bash
export ENDOREG_REPORT_PDF_RENDERER_BIN="$PWD/target/release/report_pdf_renderer"
```

Verify backend can resolve the binary:

```bash
python - <<'PY'
from endoreg_db.services.report_pdf_renderer import get_renderer_binary
print(get_renderer_binary())
PY
```

Optional local install:
```bash
install -m755 ./target/release/report_pdf_renderer ~/.local/bin/report_pdf_renderer
export ENDOREG_REPORT_PDF_RENDERER_BIN="$HOME/.local/bin/report_pdf_renderer"
```

### Production recommendation
Do not rely only on `PATH` in production.
Set an explicit env var in your service manager (systemd/docker/etc.):

```bash
ENDOREG_REPORT_PDF_RENDERER_BIN=/opt/endoreg/bin/report_pdf_renderer
```

## 2. First-Run `devenv` Bootstrap (Already Added)
The renderer repo contains a `devenv` task that auto-builds the binary on first shell entry if missing:

```bash
cd lx-report-generator
devenv shell
```

It checks for:
- `./target/release/report_pdf_renderer`

If missing, it runs:
- `cargo build --release`

## 3. Publish `report_pdf_renderer` To Cargo / crates.io

### Important prerequisites
- `cargo` and `rustc` installed
- crates.io account created
- Cargo auth token configured
- crate name available on crates.io

Current crate manifest:
- `lx-report-generator/Cargo.toml`

### 3.1. Login to Cargo (one-time per machine)
Generate a token from crates.io account settings, then:

```bash
cargo login <YOUR_CRATES_IO_TOKEN>
```

### 3.2. Validate package metadata before publish
Inside renderer directory:

```bash
cd lx-report-generator
cargo check
cargo test || true
cargo package
```

Notes:
- `cargo package` is the best preflight check because it validates publishable contents.
- `cargo test || true` is acceptable temporarily if no tests exist yet.

### 3.3. Set version and metadata in `Cargo.toml`
Before publishing, ensure these fields are correct:
- `name`
- `version` (must increase every publish)
- `license`
- `description` (recommended)
- `repository` (recommended)
- `readme` (recommended)

Example additions:
```toml
[package]
name = "report_pdf_renderer"
version = "0.1.0"
description = "Standalone PDF renderer for endoscopy report template payloads"
repository = "https://github.com/<org>/<repo>"
readme = "README.md"
license = "MIT"
```

### 3.4. Publish
```bash
cd lx-report-generator
cargo publish
```

### 3.5. Tag release (recommended)
In the renderer repo (it currently has its own `.git`):
```bash
git tag v0.1.0
git push origin v0.1.0
```

## 4. Is publishing to Cargo enough for backend runtime?
Not by itself.

Publishing to Cargo makes the source crate available, but `endoreg_db` still needs a compiled binary at runtime.
You still need one of:
- build binary in CI and ship in your app image/server
- install binary separately on server and set `ENDOREG_REPORT_PDF_RENDERER_BIN`

## 5. Suggested CI/CD release flow (recommended)
1. Build Rust binary in CI (`cargo build --release`)
2. Archive/publish binary artifact (or bake into backend container)
3. Deploy backend + binary together
4. Set `ENDOREG_REPORT_PDF_RENDERER_BIN`
5. (Optional) publish crate source to Cargo for public reuse/versioning

## 6. Troubleshooting

### `cargo: command not found`
Use the standalone module's Nix shell:
```bash
cd /home/admin/endoreg-db/lx-report-generator
devenv shell
```

### Backend falls back to minimal PDF renderer
Check:
- `echo $ENDOREG_REPORT_PDF_RENDERER_BIN`
- binary exists and is executable
- `report_pdf_renderer --help`

### `cargo publish` fails because crate name exists
Pick a unique crate name (recommended namespaced):
- `endoreg-report-pdf-renderer`
- `endoreg_db_report_pdf_renderer`

Then update `Cargo.toml` package `name`.
