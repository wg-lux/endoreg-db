# Prototype Release 0.8.1

> **Current operator action (2026-11-02, unreleased):** Before upgrading a v1 machine or resuming media migration, read the [LXENC v2 changes](../changelog.md) and [upgrade/resume procedure](../docs/secret_rotation.md#v1-machines-and-interrupted-media-migrations). Preserve existing keys and backups; deploy compatible readers before v2 writes. The historical release content below is unchanged.

> Historical release note: this file records statements made for version 0.8.1. It is not evidence of the current production state. The current package version and maturity classifier are defined in `pyproject.toml`.

## Summary
- First public prototype build published for evaluation on PyPI.
- Bundles the current Django application state, including experimental services and admin utilities.
- Known typing lint noise (mostly Django-related) is tracked for a later hardening pass and does not block this release.

## Historical quality snapshot
- The original release note stated that `pytest -q` was run, with fixture failures treated as out of scope for the prototype. This repository does not retain reproducible command output for that run.
- The original release note stated that `python -m build` and `python -m twine check dist/*` were run before upload. This repository does not retain reproducible command output for those runs.

## Next steps
- Triage remaining typing warnings and optional test failures before promoting the package beyond prototype status.
- Expand documentation around deployment and configuration on the next iteration.
