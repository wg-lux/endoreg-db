# Handoff: PyPI Release (`endoreg-db`)

This project uses:
- `lx-dtypes` as a Python dependency from PyPI
- local `lx-data-models/` submodule for development convenience
- local Rust renderer source in `tools/report_pdf_renderer_rust/` (external runtime binary, not bundled in the Python package)

## What changed for PyPI readiness
`pyproject.toml` now excludes these from wheel/sdist builds:
- `lx-data-models/`
- `tools/` (including `report_pdf_renderer_rust`)
- local devenv/direnv/git artifacts
- tests/storage/htmlcov

This prevents shipping local submodules/tooling inside `endoreg-db` PyPI artifacts.

## Versioning model (important)
`endoreg-db` version is **dynamic from git tags** (`hatch-vcs`).

You do **not** edit a static version string in `pyproject.toml`.

To publish a new version, create a new git tag (example):
```bash
git tag v0.2.0
git push origin v0.2.0
```

Builds created from that tag will resolve the package version from VCS metadata.

## Release checklist (repo root)
1. Ensure `lx-dtypes` dependency version is correct in `pyproject.toml`
- `endoreg-db` should depend on a published `lx-dtypes` version
- do not rely on local `lx-data-models/` being present for production installs

2. Ensure Rust renderer deployment strategy is documented for ops
- `report_pdf_renderer_rust` is **not** included in PyPI package
- runtime must provide compiled binary and set:
```bash
ENDOREG_REPORT_PDF_RENDERER_BIN=/path/to/report_pdf_renderer
```

3. Build and inspect artifacts (preflight)
```bash
make pypi-clean
make pypi-build-check
```

Verify the sdist listing does **not** contain:
- `lx-data-models/`
- `tools/report_pdf_renderer_rust/`

4. Run smoke checks (recommended)
```bash
pytest -q tests/views/report/test_patient_examination_report_viewset.py::PatientExaminationReportSegmentFrameSelectorTests::test_segment_frame_selector_get_auto_creates_draft_report
pytest -q tests/views/report/test_report_stream.py::ReportStreamViewTests::test_pdf_stream_download_nginx_headers
```

5. Create/push release tag (version source)
```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

6. Publish to PyPI
```bash
python -m twine upload dist/*
```

## Notes on `lx-data-models` submodule
The submodule is useful in development, but published `endoreg-db` should install against:
- `lx-dtypes` from PyPI (declared dependency)

Runtime code in `endoreg_db/urls/__init__.py` only prepends local `lx-data-models/` to `sys.path` if present.
This is safe for PyPI installs because it falls back to the installed `lx-dtypes` package.

## Notes on `report_pdf_renderer_rust`
The Rust renderer is integrated as an **optional external binary**.

`endoreg-db` runtime behavior:
- If renderer binary is found (`ENDOREG_REPORT_PDF_RENDERER_BIN` or PATH), use it.
- Otherwise, fallback to internal minimal PDF generation.

This is intentional and keeps PyPI publication platform-independent.

## Optional: publish Rust renderer separately
If you publish `report_pdf_renderer_rust` to Cargo (`crates.io`), that is **source publication**, not backend runtime deployment.
You still need a compiled binary on the target environment.

See:
- `docs/handoff_report_pdf_renderer.md`
