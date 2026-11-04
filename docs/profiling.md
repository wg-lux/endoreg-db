# Profile local import and streaming work

Use Pyinstrument to find where a selected workflow spends time. Use the existing
function profiler when you need Python call counts and thread CPU time.

## Choose the measurement

| Tool | Measures | Does not establish |
| --- | --- | --- |
| Pyinstrument | Sampled elapsed time in the current Python call stack, including waits | Production request frequency, exact call counts or native encoder internals |
| `profile_functions` | Deterministic function statistics using thread CPU time | Time spent waiting, other threads or remote workers |
| Workload timing events | Instrumented operation and phase durations | A Python stack profile |

A file with high inclusive time may simply call an expensive dependency. Inspect
its children before changing it. Nested file totals overlap and must not be added.

## Run a bounded local screen

Use the prepared project environment; Pyinstrument is included in development
dependencies. Check `pgrep -af 'pytest|py.test'` first. Run one foreground test
command, without coverage or parallel pytest workers. Concurrent host activity
must be recorded when comparing timings.

From the repository root:

```bash
mkdir -p profile
RUN_VIDEO_TESTS=true SKIP_EXPENSIVE_TESTS=false \
.devenv/state/venv/bin/python -m pyinstrument \
  --interval 0.005 --show-all -p processor_options.filter_threshold=0 \
  -r json -o profile/local-workflows.json -m pytest \
  tests/services/test_report_import_service_unit.py \
  tests/services/test_video_import_service.py::test_import_and_anonymize_locks_original_before_sensitive_copy \
  tests/import_files/test_import_checkpoints.py \
  tests/services/test_hls_media.py::test_materialize_video_hls_real_ffmpeg_commits_staged_output \
  tests/views/video/test_hls_stream.py -q --tb=short
```

This uses isolated test storage and PostgreSQL. Report/video orchestration tests
replace expensive boundaries; checkpoint tests exercise encrypted persistence;
HLS tests invoke real FFmpeg. API tests do not run an external proxy, browser or
identity provider. This is not a complete anonymization benchmark.

For interactive exploration, use `-r html -o profile/local-workflows.html` on a
subsequent run of the same workload. A command profiles its own process: broker
dispatch does not capture the Celery worker that later executes it.

## Rank files without counting test setup

The JSON includes startup, collection, fixtures, test bodies and teardown.
Ranking that whole tree can mostly describe database setup. For application
screening, select outermost `test_*` frames under `tests/`, then aggregate
`endoreg_db/` paths within those subtrees:

```python
import json
from collections import defaultdict
from pathlib import Path

profile = json.loads(Path("profile/local-workflows.json").read_text())
root = str(Path.cwd()) + "/"
seconds = defaultdict(float)
bodies = []

def find_bodies(frame):
    if "/tests/" in frame["file_path"] and frame["function"].startswith("test_"):
        bodies.append(frame)
    else:
        for child in frame["children"]:
            find_bodies(child)

def accumulate(frame, ancestors):
    path = frame["file_path"]
    if path.startswith(root + "endoreg_db/"):
        if path not in ancestors:
            seconds[path.removeprefix(root)] += frame["time"]
        ancestors = ancestors | {path}
    for child in frame["children"]:
        accumulate(child, ancestors)

find_bodies(profile["root_frame"])
for frame in bodies:
    accumulate(frame, set())
for path, elapsed in sorted(seconds.items(), key=lambda item: item[1], reverse=True)[:15]:
    print(f"{elapsed:8.3f}s  {path}")
```

Each file is counted once along a sampled ancestry path. This avoids counting
nested calls in the same file repeatedly, while retaining time in descendants.
Use the summed body-frame time as the denominator, not total session duration.
Test assertions and lazily loaded dependencies still contribute. Brief functions
may receive no sample; absence does not prove non-use or authorize deletion.

## Inspect thread CPU time

In an initialized Django shell, wrap an existing import call and its inputs:

```python
from pathlib import Path
from endoreg_db.utils.profiling import profile_functions

with profile_functions(Path("profile/imports")):
    service.import_and_anonymize(source_path, center_name, processor_name)
```

The operation executes normally, including database and media writes. Profiling
is off by default. Each outermost decorated call writes uniquely named `.txt`
(top 40 cumulative calls and elapsed wall time) and `.prof` files. Nested calls
share the profile. `cumtime` includes callees; `tottime` excludes them.
Thread CPU time excludes waiting; `elapsed_wall_seconds` includes it.

```text
python -m pstats profile/imports/SELECTED_FILE.prof
sort cumulative
stats 30
```

Replace `SELECTED_FILE.prof` with an actual output filename. Profiles are attempted
on success and failure; write errors are logged without hiding the operation's
result or original exception. Management-command `--profile-output` and
`--profile-summary-output` options use wall time instead.

## Interpretation and handling

- Record the revision and local changes, tool version, interval, workload, skips,
  failures and concurrent load. A terminated run with an empty profile is not evidence.
- Profile execution inside the relevant worker to investigate worker costs.
  FFmpeg, Rust internals and other threads need their own profiling tools.
- Profiles include source paths and function names. Keep them in ignored local
  `profile/` output; review them before sharing. Do not put clinical input paths
  or secrets in command descriptions.
- Use `workload.timing` events from `utils/workload_timing.py` for phase correlation.
  Do not interpret synthetic test throughput as production traffic volume.

Implementation: [`utils/profiling.py`](../endoreg_db/utils/profiling.py).
Scope and evidence: [CodeQuality.yml](../feature-tracking/CodeQuality.yml),
`import_function_profiling`, and [Documentation.yml](../feature-tracking/Documentation.yml).
