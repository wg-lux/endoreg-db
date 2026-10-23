# CPU profiling: imports and reimports

Profiling is **off by default**. In an initialized Django shell, wrap your existing
video import call and inputs:

```python
from pathlib import Path
from endoreg_db.utils.profiling import profile_functions

with profile_functions(Path("profile/imports")):
    service.import_and_anonymize(source_path, center_name, processor_name)
```

The same scope works around report imports and video reimports with their usual
arguments. The operation runs normally, including database and media writes.

## Read the results

Each outermost decorated call creates two uniquely named files under
`profile/imports/`, relative to the current working directory:

- **`.txt`**: top 40 calls by cumulative CPU time, plus elapsed wall duration.
- **`.prof`**: full call graph, including nested decorated calls.

Start with the text summary: **cumtime** includes called functions; **tottime**
measures the function itself. Thread CPU time excludes waiting; the separate
`elapsed_wall_seconds` includes it.

For interactive inspection, replace `SELECTED_FILE.prof` with a generated filename:

```text
python -m pstats profile/imports/SELECTED_FILE.prof
sort cumulative
stats 30
```

## Coverage and limits

Profiles cover video/report import pipelines, video reanonymization, report/video
publication, and inline/dispatched video reimports. Nested calls share one profile.

- Only the current synchronous thread is captured. Enable the scope inside a
  Celery worker to profile execution rather than just dispatch.
- FFmpeg processes and Rust worker internals require native profiling tools.
- Profiles contain function names, source-code paths, and timings—not argument
  values or media contents.
- Profiles are attempted on success and failure. Write errors are logged without
  changing the function result or hiding its original exception.

Existing management-command `--profile-output` and `--profile-summary-output`
options remain available and use wall time.

Implementation: [`utils/profiling.py`](../endoreg_db/utils/profiling.py).
Verification evidence: [`CodeQuality.yml`](../feature-tracking/CodeQuality.yml),
criterion `import_function_profiling`.
