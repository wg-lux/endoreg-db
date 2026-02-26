SHELL := /bin/sh

RUST_RENDERER_DIR := tools/report_pdf_renderer_rust
RUST_RENDERER_BIN := $(RUST_RENDERER_DIR)/target/release/report_pdf_renderer
RUST_RENDERER_EXAMPLE := $(RUST_RENDERER_DIR)/examples/report_payload.json
RUST_RENDERER_OUT ?= /tmp/report_example.pdf
LOCAL_BIN_DIR ?= $(HOME)/.local/bin
LOCAL_RENDERER_BIN := $(LOCAL_BIN_DIR)/report_pdf_renderer
MYPY_BIN ?= .devenv/state/venv/bin/mypy
MYPY_TIMEOUT ?= 240

.PHONY: help report-renderer-build report-renderer-build-devenv report-renderer-run-example report-renderer-run-example-devenv report-renderer-install report-renderer-install-devenv report-renderer-env report-renderer-clean pypi-build-check pypi-clean mypy-requirement mypy-requirement-files

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
	@echo "  pypi-build-check               Build wheel/sdist and list contents (publish preflight)"
	@echo "  pypi-clean                     Remove dist/build artifacts"
	@echo "  mypy-requirement               Run mypy on endoreg_db/models/requirement (repo config)"
	@echo "  mypy-requirement-files         Run mypy per file in requirement subtree (repo config)"
	@echo ""
	@echo "Variables:"
	@echo "  RUST_RENDERER_OUT=$(RUST_RENDERER_OUT)"
	@echo "  LOCAL_BIN_DIR=$(LOCAL_BIN_DIR)"
	@echo "  MYPY_BIN=$(MYPY_BIN)"
	@echo "  MYPY_TIMEOUT=$(MYPY_TIMEOUT)"

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

pypi-build-check:
	@if command -v python >/dev/null 2>&1; then \
		python -m build; \
	else \
		echo "python not found"; \
		exit 1; \
	fi
	@echo \"\\n== dist contents ==\"
	@ls -lh dist || true
	@echo \"\\n== sdist top-level preview ==\"
	@python -c "import tarfile, pathlib; sdists=sorted(pathlib.Path('dist').glob('*.tar.gz')); \
assert sdists, 'No sdist found'; \
tf=tarfile.open(sdists[-1], 'r:gz'); \
names=tf.getnames(); \
[print(n) for n in names[:120]]; \
tf.close()"
	@echo \"\\nCheck that lx-data-models/ and tools/report_pdf_renderer_rust/ are absent from the sdist listing above.\"

pypi-clean:
	@rm -rf dist build *.egg-info
	@echo \"Cleaned dist/build artifacts\"

mypy-requirement:
	@timeout $(MYPY_TIMEOUT)s $(MYPY_BIN) endoreg_db/models/requirement

mypy-requirement-files:
	@python - <<'PY'
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
