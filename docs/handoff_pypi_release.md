# Handoff: PyPI Release (`endoreg-db`)

This repository publishes the root Python package with a Rust extension through the `maturin` backend configured in [pyproject.toml](../pyproject.toml).

## Current version source

The publishable package version is currently a static value, not a git-derived dynamic version:

- [pyproject.toml](../pyproject.toml): `project.version`
- [package.nix](../package.nix): `version`

Before a release, bump both files to the new version.

## Platform-configurable release workflow

The release `Makefile` targets now use `maturin` directly so wheel settings can be overridden per platform.

Available knobs:

- `PYPI_DIST_DIR`: output directory, default `dist`
- `PYPI_INTERPRETER`: interpreter passed to `maturin build --interpreter`
- `PYPI_COMPATIBILITY`: compatibility tag such as `linux`, `manylinux2014`, `manylinux_2_28`, or `musllinux_1_2`
- `PYPI_ZIG`: if set to any non-empty value, adds `--zig`
- `PYPI_MATURIN_ARGS`: extra arguments forwarded to `maturin build` and `maturin sdist`
- `PYPI_REPOSITORY`: optional `twine --repository`
- `PYPI_REPOSITORY_URL`: optional `twine --repository-url`
- `PYPI_TWINE_ARGS`: extra arguments forwarded to `twine upload`

Default command resolution:

- `MATURIN_BIN=maturin`
- `TWINE_BIN=twine`
- `PYPI_COMPATIBILITY=linux`

Override them if your environment only exposes module entrypoints or wrapped commands.

Primary targets:

- `make pypi-wheel`
- `make pypi-sdist`
- `make pypi-build-check`
- `make pypi-upload`
- `make pypi-clean`

## Release checklist

1. Update version numbers:
```bash
$EDITOR pyproject.toml
$EDITOR package.nix
```

2. Commit the release candidate and ensure the tree is clean:
```bash
git status
git add pyproject.toml package.nix
git commit -m "Release vX.Y.Z"
git status
```

3. Clean previous artifacts:
```bash
make pypi-clean
```

4. Build platform-specific wheels plus sdist.

Default local Linux build:
```bash
make pypi-build-check
```

This produces a native Linux wheel. It does not try to enforce manylinux compliance.

Linux `manylinux2014`:
```bash
make pypi-build-check \
  PYPI_INTERPRETER=python3.12 \
  PYPI_COMPATIBILITY=manylinux2014
```

Linux with `zig`-assisted portability:
```bash
make pypi-build-check \
  PYPI_INTERPRETER=python3.12 \
  PYPI_COMPATIBILITY=manylinux2014 \
  PYPI_ZIG=1
```

Musl / Alpine-style target:
```bash
make pypi-build-check \
  PYPI_INTERPRETER=python3.12 \
  PYPI_COMPATIBILITY=musllinux_1_2
```

Native macOS wheel:
```bash
make pypi-build-check \
  PYPI_INTERPRETER=python3.12
```

Native Windows wheel:
```bash
make pypi-build-check \
  PYPI_INTERPRETER=python
```

Notes:

- Build Linux wheels on Linux, macOS wheels on macOS, and Windows wheels on Windows unless you have a deliberate cross-compilation setup.
- `PYPI_COMPATIBILITY=linux` is the default because it works for local native builds on the current host.
- `PYPI_COMPATIBILITY` is mainly relevant for Linux wheel tagging and repair behavior.
- `manylinux*` wheels must be built in a compatible manylinux environment or with an explicit cross-compilation strategy. Building them on a newer general host glibc often fails compliance checks.
- The sdist is platform-independent and is produced together with the wheel by `make pypi-build-check`.

5. Inspect the artifacts in `dist/` and confirm the sdist excludes local-only directories:

- `lx-data-models/`
- `lx-report-generator/`
- `lx-terminology-editor/`
- `tools/`

6. Upload to PyPI or TestPyPI.

PyPI:
```bash
make pypi-upload
```

TestPyPI:
```bash
make pypi-upload \
  PYPI_REPOSITORY_URL=https://test.pypi.org/legacy/
```

7. Tag and push the release if you want git history to match the published version:
```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin prototype
git push origin vX.Y.Z
```

## Separate uploads per platform

If you publish multiple wheels for the same version, build each platform artifact into the same `dist/` layout and upload only after all wheels are present alongside the sdist.

Example sequence:

1. Build Linux wheel on Linux host
2. Copy resulting wheel into shared `dist/`
3. Build macOS wheel on macOS host
4. Copy resulting wheel into shared `dist/`
5. Build Windows wheel on Windows host
6. Copy resulting wheel into shared `dist/`
7. Build the sdist once
8. Run `make pypi-upload`

## Notes on bundled and external components

`endoreg-db` publishes the Python package plus the `endoreg_db.endoreg_rust_backend` extension module built from:

- [rust/endoreg_rust_backend/Cargo.toml](../rust/endoreg_rust_backend/Cargo.toml)

The standalone report renderer remains an external runtime binary and is not bundled into the PyPI package:

- `tools/report_pdf_renderer_rust/`
- `lx-report-generator/`

Operational deployments that want the external renderer still need to provide the binary and set:

```bash
export ENDOREG_REPORT_PDF_RENDERER_BIN=/path/to/report_pdf_renderer
```
