SHELL := /bin/sh

RUST_RENDERER_DIR := tools/report_pdf_renderer_rust
RUST_RENDERER_BIN := $(RUST_RENDERER_DIR)/target/release/report_pdf_renderer
RUST_RENDERER_EXAMPLE := $(RUST_RENDERER_DIR)/examples/report_payload.json
RUST_RENDERER_OUT ?= /tmp/report_example.pdf
LOCAL_BIN_DIR ?= $(HOME)/.local/bin
LOCAL_RENDERER_BIN := $(LOCAL_BIN_DIR)/report_pdf_renderer
MYPY_BIN ?= .devenv/state/venv/bin/mypy
MYPY_TIMEOUT ?= 240
PYTHON_BIN ?= python
MATURIN_BIN ?= maturin
TWINE_BIN ?= twine
PYPI_DIST_DIR ?= dist
PYPI_MANIFEST_PATH ?= rust/endoreg_rust_backend/Cargo.toml
PYPI_INTERPRETER ?=
PYPI_COMPATIBILITY ?= linux
PYPI_ZIG ?=
PYPI_MATURIN_ARGS ?=
PYPI_TWINE_ARGS ?=
PYPI_REPOSITORY ?=
PYPI_REPOSITORY_URL ?=

.PHONY: help report-renderer-build report-renderer-build-devenv report-renderer-run-example report-renderer-run-example-devenv report-renderer-install report-renderer-install-devenv report-renderer-env report-renderer-clean pypi-wheel pypi-sdist pypi-build-check pypi-clean pypi-upload mypy-requirement mypy-requirement-files

help:
	@echo "Available targets:"
	@echo "  report-renderer-build           Build Rust PDF renderer (uses cargo if available)"
	@echo "  report-renderer-build-devenv    Build renderer inside devenv shell"
	@echo "  report-renderer-run-example     Build if needed, render example payload to PDF"
	@echo "  report-renderer-run-example-devenv  Same, but via devenv shell"
	@echo "  report-renderer-install         Install binary to ~/.local/bin"
	@echo "  report-renderer-install-devenv  Install binary via devenv shell"
	@echo "  report-renderer-env             Print export command for backend integration"
	@echo "  report-renderer-clean           Remove renderer build artifacts"
	@echo "  pypi-wheel                     Build a maturin wheel with configurable platform settings"
	@echo "  pypi-sdist                     Build an sdist"
	@echo "  pypi-build-check               Build wheel/sdist and list contents (publish preflight)"
	@echo "  pypi-clean                     Remove dist/build artifacts"
	@echo "  pypi-upload                    Upload dist artifacts with twine"
	@echo "  mypy-requirement               Run mypy on endoreg_db/models/requirement (repo config)"
	@echo "  mypy-requirement-files         Run mypy per file in requirement subtree (repo config)"
	@echo ""
	@echo "Variables:"
	@echo "  RUST_RENDERER_OUT=$(RUST_RENDERER_OUT)"
	@echo "  LOCAL_BIN_DIR=$(LOCAL_BIN_DIR)"
	@echo "  MYPY_BIN=$(MYPY_BIN)"
	@echo "  MYPY_TIMEOUT=$(MYPY_TIMEOUT)"
	@echo "  PYTHON_BIN=$(PYTHON_BIN)"
	@echo "  MATURIN_BIN=$(MATURIN_BIN)"
	@echo "  TWINE_BIN=$(TWINE_BIN)"
	@echo "  PYPI_DIST_DIR=$(PYPI_DIST_DIR)"
	@echo "  PYPI_MANIFEST_PATH=$(PYPI_MANIFEST_PATH)"
	@echo "  PYPI_INTERPRETER=$(PYPI_INTERPRETER)"
	@echo "  PYPI_COMPATIBILITY=$(PYPI_COMPATIBILITY)  # set to manylinux2014, manylinux_2_28, musllinux_1_2, etc. when needed"
	@echo "  PYPI_ZIG=$(PYPI_ZIG)"
	@echo "  PYPI_MATURIN_ARGS=$(PYPI_MATURIN_ARGS)"
	@echo "  PYPI_TWINE_ARGS=$(PYPI_TWINE_ARGS)"
	@echo "  PYPI_REPOSITORY=$(PYPI_REPOSITORY)"
	@echo "  PYPI_REPOSITORY_URL=$(PYPI_REPOSITORY_URL)"

report-renderer-build:
	@if command -v cargo >/dev/null 2>&1; then \
		cd $(RUST_RENDERER_DIR) && cargo build --release; \
	else \
		echo "cargo not found. Use 'make report-renderer-build-devenv'"; \
		exit 1; \
	fi

report-renderer-build-devenv:
	@cd $(RUST_RENDERER_DIR) && devenv shell -- cargo build --release

report-renderer-run-example: report-renderer-build
	@$(RUST_RENDERER_BIN) --input $(RUST_RENDERER_EXAMPLE) --output $(RUST_RENDERER_OUT)
	@echo "Generated: $(RUST_RENDERER_OUT)"

report-renderer-run-example-devenv:
	@cd $(RUST_RENDERER_DIR) && devenv shell -- sh -lc './target/release/report_pdf_renderer --input examples/report_payload.json --output $(RUST_RENDERER_OUT)'
	@echo "Generated: $(RUST_RENDERER_OUT)"

report-renderer-install: report-renderer-build
	@mkdir -p $(LOCAL_BIN_DIR)
	@install -m755 $(RUST_RENDERER_BIN) $(LOCAL_RENDERER_BIN)
	@echo "Installed: $(LOCAL_RENDERER_BIN)"
	@echo "Set backend env var:"
	@echo "  export ENDOREG_REPORT_PDF_RENDERER_BIN=$(LOCAL_RENDERER_BIN)"

report-renderer-install-devenv: report-renderer-build-devenv
	@mkdir -p $(LOCAL_BIN_DIR)
	@install -m755 $(RUST_RENDERER_BIN) $(LOCAL_RENDERER_BIN)
	@echo "Installed: $(LOCAL_RENDERER_BIN)"
	@echo "Set backend env var:"
	@echo "  export ENDOREG_REPORT_PDF_RENDERER_BIN=$(LOCAL_RENDERER_BIN)"

report-renderer-env:
	@echo "export ENDOREG_REPORT_PDF_RENDERER_BIN=$(abspath $(LOCAL_RENDERER_BIN))"

report-renderer-clean:
	@rm -rf $(RUST_RENDERER_DIR)/target
	@echo "Cleaned $(RUST_RENDERER_DIR)/target"

# Use a specific platform tag for Zig to prevent the empty "." tag
ZIG_PLATFORM := x86_64-unknown-linux-gnu

pypi-wheel:
	@echo "Building Rust Backend Extension and Main Django Wheel with Zig/Manylinux..."
	@mkdir -p $(PYPI_DIST_DIR)
	# Unset the Nix-specific platform override to let Maturin/Zig do their job
	unset _PYTHON_HOST_PLATFORM; \
	$(MATURIN_BIN) build --release \
		--zig \
		--compatibility manylinux2014 \
		--out $(PYPI_DIST_DIR)
		
pypi-sdist:
	@mkdir -p $(PYPI_DIST_DIR)
	@set -e; \
	args="--out $(PYPI_DIST_DIR)"; \
	if [ -n "$(PYPI_MATURIN_ARGS)" ]; then \
		args="$$args $(PYPI_MATURIN_ARGS)"; \
	fi; \
	echo "$(MATURIN_BIN) sdist $$args"; \
	$(MATURIN_BIN) sdist $$args

pypi-build-check: pypi-wheel pypi-sdist
	@echo \"\\n== dist contents ==\"
	@ls -lh $(PYPI_DIST_DIR) || true
	@echo \"\\n== sdist top-level preview ==\"
	@$(PYTHON_BIN) -c "import tarfile, pathlib; sdists=sorted(pathlib.Path('$(PYPI_DIST_DIR)').glob('*.tar.gz')); \
assert sdists, 'No sdist found'; \
tf=tarfile.open(sdists[-1], 'r:gz'); \
names=tf.getnames(); \
[print(n) for n in names[:120]]; \
tf.close()"
	@echo \"\\nCheck that lx-data-models/ and tools/report_pdf_renderer_rust/ are absent from the sdist listing above.\"

pypi-clean:
	@rm -rf $(PYPI_DIST_DIR) build *.egg-info
	@echo \"Cleaned dist/build artifacts\"

pypi-upload:
	@set -e; \
	args="$(PYPI_TWINE_ARGS)"; \
	if [ -n "$(PYPI_REPOSITORY)" ]; then \
		args="$$args --repository $(PYPI_REPOSITORY)"; \
	fi; \
	if [ -n "$(PYPI_REPOSITORY_URL)" ]; then \
		args="$$args --repository-url $(PYPI_REPOSITORY_URL)"; \
	fi; \
	echo "$(TWINE_BIN) upload $$args $(PYPI_DIST_DIR)/*"; \
	$(TWINE_BIN) upload $$args $(PYPI_DIST_DIR)/*

mypy-requirement:
	@timeout $(MYPY_TIMEOUT)s $(MYPY_BIN) endoreg_db/models/requirement

mypy-requirement-files:
	@python - <<-'PY'
	from pathlib import Path
	import subprocess
	import sys

	root = Path.cwd()
	files = sorted((root / "endoreg_db/models/requirement").rglob("*.py"))
	mypy_bin = root / ".devenv/state/venv/bin/mypy"
	timeout_s = "$(MYPY_TIMEOUT)"
	failed = False

	for file_path in files:
	    rel = file_path.relative_to(root)
	    print(f"== {rel} ==")
	    proc = subprocess.run(
	        ["timeout", f"{timeout_s}s", str(mypy_bin), str(rel)],
	        cwd=root,
	        text=True,
	        capture_output=True,
	    )
	    output = (proc.stdout or "") + (proc.stderr or "")
	    filtered = [ln for ln in output.splitlines() if ln and not ln.startswith("20")]
	    if proc.returncode == 0:
	        print("OK")
	        continue
	    failed = True
	    print("\n".join(filtered[-20:]))
	    break

	sys.exit(1 if failed else 0)
	PY
