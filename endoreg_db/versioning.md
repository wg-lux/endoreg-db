### Static package versioning with Maturin

This project builds its Python artifacts with Maturin. The release version is
the static `[project].version` value in `pyproject.toml`; `uv.lock` must contain
the same version for the editable `endoreg-db` package. Git tags document
releases, but they do not calculate or override the package version.

#### How It Works

1. **Unique version:** Choose a version that has not already been published to
   PyPI and update `pyproject.toml`.
2. **Locked metadata:** Run `uv lock` and verify it with `uv lock --check`.
3. **Clean release commit:** Build the final upload artifacts from the exact,
   committed source that will receive the matching tag.

#### Release Workflow

1. **Prepare Changes:**
Make your code changes, update `pyproject.toml` dependencies if needed, and commit everything.
```bash
git add .
git commit -m "Prepare release 1.0.15.0"

```


2. **Ensure Clean State:**
Verify there are no uncommitted changes (no "dirty" state).
```bash
git status
# Output must say: "nothing to commit, working tree clean"

```


*If you have untracked changes you don't want to commit yet, run `git stash`.*
3. **Tag the Release:**
Create an annotated tag for the new version.
```bash
git tag -a v1.0.15.0 -m "Release v1.0.15.0"

```


4. **Build:**
Generate the distribution packages.
```bash
make pypi-clean
make pypi-build-check
.devenv/state/venv/bin/twine check dist/*

```


*Check:* Wheel and source-distribution filenames must contain the exact version
from `pyproject.toml`, and the wheel must contain the migration named by
`endoreg_db/migrations/max_migration.txt`.
5. **Publish:**
Upload to PyPI.
```bash
make pypi-upload

```


6. **Push:**
Push the commit and the tag to the remote repository.
```bash
git push origin main --tags

```



#### Troubleshooting

**Problem:** PyPI reports that the filename or version already exists.

**Cause:** `[project].version` was already published. PyPI artifacts are
immutable and cannot be replaced.

**Fix:** Choose the next release version, update `pyproject.toml`, run `uv lock`,
commit the complete candidate, rebuild, and tag that exact commit.
