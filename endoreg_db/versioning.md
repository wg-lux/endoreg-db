### Dynamic Versioning with Hatch-VCS

This project uses `hatch-vcs` to automatically determine the package version based on Git tags. This ensures that PyPI releases always match the Git tag exactly, without manually editing version files.

#### How It Works

1. **Clean State:** If the commit matches a tag (e.g., `v1.0.0`) and the working directory is clean, the version is `1.0.0`.
2. **Dirty State:** If files are modified or uncommitted, `hatch` appends a local identifier (e.g., `1.0.1.dev0+g<hash>.d<date>`). **PyPI will reject these uploads.**
3. **Distance State:** If you are ahead of a tag by commits, it generates a dev version (e.g., `1.0.1.dev4+g<hash>`).

#### Release Workflow

1. **Prepare Changes:**
Make your code changes, update `pyproject.toml` dependencies if needed, and commit everything.
```bash
git add .
git commit -m "Update dependencies for release"

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
git tag -a v0.8.9.16 -m "Release v0.8.9.16"

```


4. **Build:**
Generate the distribution packages.
```bash
rm -rf /dist #ensure empty dist
python -m build

```


*Check:* The filename in `dist/` should look like `endoreg_db-0.8.9.16.tar.gz` (clean version).
5. **Publish:**
Upload to PyPI.
```bash
python -m twine upload dist/*

```


6. **Push:**
Push the commit and the tag to the remote repository.
```bash
git push origin main --tags

```



#### Troubleshooting

**Problem:** `build` generates a version like `0.8.9.17.dev0+g...`
**Cause:** Your working directory is "dirty" (uncommitted changes).
**Fix:**

1. Run `git status` to identify modified files.
2. Commit them, add them to `.gitignore`, or stash them (`git stash`).
3. Re-run the build.

**Problem:** `HTTPError: 400 Bad Request ... use of local versions is not allowed`
**Cause:** You attempted to upload a "dirty" build to PyPI.
**Fix:** Delete `dist/`, clean your git state (stash/commit), rebuild, and upload again.