#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
PYTHON="${PYTHON:-$ROOT_DIR/.devenv/state/venv/bin/python}"
MANAGE_PY="${MANAGE_PY:-$ROOT_DIR/manage.py}"
DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-endoreg_db.config.settings.dev}"
PROFILE_DIR="${PROFILE_DIR:-$ROOT_DIR/profile/$RUN_ID}"
VIDEO_PATH="${VIDEO_PATH:-$ROOT_DIR/tests/assets/test_small_intestine.mp4}"
CENTER_NAME="${CENTER_NAME:-university_hospital_wuerzburg}"
PROCESSOR_NAME="${PROCESSOR_NAME:-olympus_cv_1500}"
PROFILE_SORT="${PROFILE_SORT:-cumulative}"
PROFILE_LIMIT="${PROFILE_LIMIT:-80}"
MASTER_KEY_FILE="${LX_ANNOTATE_MASTER_KEY_FILE:-}"
DRY_RUN=0

RUN_MIGRATE="${RUN_MIGRATE:-1}"
RUN_LOAD_BASE_DATA="${RUN_LOAD_BASE_DATA:-1}"
RUN_IMPORT="${RUN_IMPORT:-1}"
RUN_VIDEO_STREAMING="${RUN_VIDEO_STREAMING:-1}"
RUN_HLS_MATERIALIZATION_FOR_STREAMING="${RUN_HLS_MATERIALIZATION_FOR_STREAMING:-1}"
RUN_SEGMENT_UPDATES="${RUN_SEGMENT_UPDATES:-1}"
RUN_ENSURE_SEGMENT_ANNOTATIONS="${RUN_ENSURE_SEGMENT_ANNOTATIONS:-1}"
RUN_RECONCILE_FRAME_SEGMENTS="${RUN_RECONCILE_FRAME_SEGMENTS:-1}"
CONVERT_CALLGRIND="${CONVERT_CALLGRIND:-auto}"

VIDEO_STREAMING_PROFILE_ENDPOINT="${VIDEO_STREAMING_PROFILE_ENDPOINT:-hls}"
VIDEO_STREAMING_PROFILE_ITERATIONS="${VIDEO_STREAMING_PROFILE_ITERATIONS:-50}"
VIDEO_STREAMING_PROFILE_LIMIT="${VIDEO_STREAMING_PROFILE_LIMIT:-1}"

SEGMENT_PROFILE_SEGMENTS="${SEGMENT_PROFILE_SEGMENTS:-1000}"
SEGMENT_PROFILE_CREATE_COUNT="${SEGMENT_PROFILE_CREATE_COUNT:-250}"
SEGMENT_PROFILE_UPDATE_COUNT="${SEGMENT_PROFILE_UPDATE_COUNT:-250}"
SEGMENT_PROFILE_DELETE_COUNT="${SEGMENT_PROFILE_DELETE_COUNT:-250}"
SEGMENT_PROFILE_FRAME_COUNT="${SEGMENT_PROFILE_FRAME_COUNT:-20000}"
SEGMENT_PROFILE_REMOVED_FRAME_STEP="${SEGMENT_PROFILE_REMOVED_FRAME_STEP:-25}"

PROFILE_RUNNERS=(
  run_import_pipeline_profile
  run_video_streaming_profile
  run_segment_updates_profile
  run_ensure_segment_annotations_profile
  run_reconcile_frame_segments_profile
)

usage() {
  cat <<'EOF'
Usage:
  scripts/run_profiling_suite.sh [options]

Required:
  Provide LX_ANNOTATE_MASTER_KEY in the environment, or pass --master-key-file.
  The script exports LX_ANNOTATE_MASTER_KEY for all child manage.py commands.

Options:
  --profile-dir DIR          Directory for .prof, summary, stdout, stderr artifacts.
  --video PATH               Video used for the import pipeline profile.
  --master-key-file PATH     Read LX_ANNOTATE_MASTER_KEY from this file.
  --skip-migrate             Do not run manage.py migrate.
  --skip-load-base-data      Do not run manage.py load_base_db_data.
  --skip-import              Do not run kcache_video_import.
  --skip-video-streaming     Do not run profile_video_streaming.
  --skip-segment-updates     Do not run profile_segment_updates.
  --skip-ensure-segments     Do not run ensure_segment_annotations.
  --skip-reconcile           Do not run reconcile_frame_segment_annotations.
  --dry-run                  Print commands without executing them.
  -h, --help                 Show this help.

Common environment overrides:
  PYTHON, MANAGE_PY, PROFILE_SORT, PROFILE_LIMIT, CENTER_NAME, PROCESSOR_NAME,
  VIDEO_PATH, RUN_ID, CONVERT_CALLGRIND.

Extending:
  Add a run_<name>_profile function and append its name to PROFILE_RUNNERS.
  Use run_profiled_manage <artifact-name> <management-command> [args...].
  The import pipeline profile asserts that the anonymizer produced a processed artifact.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

clean_profiles_folder() {
  local target_dir="$HOME/$PROFILE_DIR"
  local days_old=7  # Change this to whatever age you want
  
  # Ensure the directory actually exists before running the command
  if [ -d "$target_dir" ]; then
    find "$target_dir" -type f -mtime +"$days_old" -delete
  else
    echo "Directory $target_dir does not exist."
  fi
}

require_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || die "$option requires a value"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --profile-dir)
        require_value "$1" "${2:-}"
        PROFILE_DIR="$2"
        shift 2
        ;;
      --video)
        require_value "$1" "${2:-}"
        VIDEO_PATH="$2"
        shift 2
        ;;
      --master-key-file)
        require_value "$1" "${2:-}"
        MASTER_KEY_FILE="$2"
        shift 2
        ;;
      --skip-migrate)
        RUN_MIGRATE=0
        shift
        ;;
      --skip-load-base-data)
        RUN_LOAD_BASE_DATA=0
        shift
        ;;
      --skip-import)
        RUN_IMPORT=0
        shift
        ;;
      --skip-video-streaming)
        RUN_VIDEO_STREAMING=0
        shift
        ;;
      --skip-segment-updates)
        RUN_SEGMENT_UPDATES=0
        shift
        ;;
      --skip-ensure-segments)
        RUN_ENSURE_SEGMENT_ANNOTATIONS=0
        shift
        ;;
      --skip-reconcile)
        RUN_RECONCILE_FRAME_SEGMENTS=0
        shift
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done
}

quote_command() {
  printf '%q ' "$@"
}

ensure_runtime() {
  [[ -x "$PYTHON" ]] || die "python executable not found: $PYTHON"
  [[ -f "$MANAGE_PY" ]] || die "manage.py not found: $MANAGE_PY"

  if [[ -z "${LX_ANNOTATE_MASTER_KEY:-}" && -n "$MASTER_KEY_FILE" ]]; then
    [[ -r "$MASTER_KEY_FILE" ]] || die "master key file is not readable: $MASTER_KEY_FILE"
    LX_ANNOTATE_MASTER_KEY="$(tr -d '[:space:]' < "$MASTER_KEY_FILE")"
  fi

  [[ -n "${LX_ANNOTATE_MASTER_KEY:-}" ]] || die \
    "LX_ANNOTATE_MASTER_KEY is required. Set it in the environment or pass --master-key-file."
  export LX_ANNOTATE_MASTER_KEY
  export DJANGO_SETTINGS_MODULE

  export WATCHER_STABLE_AFTER_SECONDS="${WATCHER_STABLE_AFTER_SECONDS:-0}"
  export WATCHER_POLL_INTERVAL_SECONDS="${WATCHER_POLL_INTERVAL_SECONDS:-0.01}"
  export SERVE_WITH_NGINX="${SERVE_WITH_NGINX:-true}"
  export NGINX_PROTECTED_MEDIA_URL="${NGINX_PROTECTED_MEDIA_URL:-/protected_media/}"

  if [[ "$RUN_IMPORT" == "1" ]]; then
    [[ -f "$VIDEO_PATH" ]] || die "import profile video not found: $VIDEO_PATH"
  fi

  mkdir -p "$PROFILE_DIR"
}

run_logged() {
  local name="$1"
  shift
  local stdout_log="$PROFILE_DIR/${name}.stdout.log"
  local stderr_log="$PROFILE_DIR/${name}.stderr.log"
  local command_log="$PROFILE_DIR/${name}.command.txt"

  printf '\n==> %s\n' "$name"
  quote_command "$@" | tee "$command_log"
  printf '\n'

  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  if "$@" >"$stdout_log" 2>"$stderr_log"; then
    printf 'ok: %s\n' "$name"
    printf '  stdout: %s\n' "$stdout_log"
    printf '  stderr: %s\n' "$stderr_log"
    return 0
  fi

  printf 'failed: %s\n' "$name" >&2
  printf 'stdout tail (%s):\n' "$stdout_log" >&2
  tail -n 80 "$stdout_log" >&2 || true
  printf 'stderr tail (%s):\n' "$stderr_log" >&2
  tail -n 120 "$stderr_log" >&2 || true
  exit 1
}

run_manage() {
  local name="$1"
  shift
  run_logged "$name" "$PYTHON" "$MANAGE_PY" "$@"
}

run_profiled_manage() {
  local artifact_name="$1"
  shift
  local profile_path="$PROFILE_DIR/${artifact_name}.prof"
  local summary_path="$PROFILE_DIR/${artifact_name}.txt"

  run_manage "$artifact_name" "$@" \
    --profile-output "$profile_path" \
    --profile-summary-output "$summary_path" \
    --profile-sort "$PROFILE_SORT" \
    --profile-limit "$PROFILE_LIMIT"

  convert_profile_if_available "$artifact_name" "$profile_path"
}

assert_import_pipeline_anonymized() {
  local stdout_log="$PROFILE_DIR/import_pipeline.stdout.log"

  [[ "$DRY_RUN" == "0" ]] || return 0
  [[ -s "$stdout_log" ]] || die "import_pipeline stdout log is missing or empty: $stdout_log"

  "$PYTHON" - "$stdout_log" <<'PY'
import json
import sys
from pathlib import Path

payload_path = Path(sys.argv[1])


def load_json_payload(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    best_payload: dict[str, object] | None = None
    best_length = -1
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if not text[index + end :].strip():
            return value
        if end > best_length:
            best_payload = value
            best_length = end
    if best_payload is None:
        raise SystemExit(
            f"import_pipeline assertion failed: no JSON payload found in {path}"
        )
    return best_payload


payload = load_json_payload(payload_path)
video = payload.get("video")
if not isinstance(video, dict):
    raise SystemExit("import_pipeline assertion failed: missing video payload")

checks = {
    "status": payload.get("status") == "anonymized",
    "inline_ingest_ran": payload.get("inline_ingest_ran") is True,
    "watched_path_exists": payload.get("watched_path_exists") is False,
    "video.anonymized": video.get("anonymized") is True,
    "video.sensitive_meta_processed": video.get("sensitive_meta_processed") is True,
    "video.processed_video_hash": bool(video.get("processed_video_hash")),
    "video.processed_file": bool(video.get("processed_file")),
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit(
        "import_pipeline assertion failed: " + ", ".join(failed)
    )
PY
}

convert_profile_if_available() {
  local artifact_name="$1"
  local profile_path="$2"
  local callgrind_path="$PROFILE_DIR/callgrind.out.${artifact_name}"

  [[ "$DRY_RUN" == "0" ]] || return 0
  [[ -s "$profile_path" ]] || return 0
  [[ "$CONVERT_CALLGRIND" != "0" ]] || return 0

  if command -v pyprof2calltree >/dev/null 2>&1; then
    run_logged "${artifact_name}.callgrind" \
      pyprof2calltree -i "$profile_path" -o "$callgrind_path"
    return 0
  fi

  if "$PYTHON" -m pyprof2calltree -h >/dev/null 2>&1; then
    run_logged "${artifact_name}.callgrind" \
      "$PYTHON" -m pyprof2calltree -i "$profile_path" -o "$callgrind_path"
    return 0
  fi

  if [[ "$CONVERT_CALLGRIND" == "1" ]]; then
    die "CONVERT_CALLGRIND=1 but pyprof2calltree is not available"
  fi

  printf 'skip: pyprof2calltree not available for %s\n' "$artifact_name"
}

run_setup_steps() {
  if [[ "$RUN_MIGRATE" == "1" ]]; then
    run_manage migrate migrate --noinput
  fi

  if [[ "$RUN_LOAD_BASE_DATA" == "1" ]]; then
    run_manage load_base_db_data load_base_db_data
  fi
}

run_import_pipeline_profile() {
  [[ "$RUN_IMPORT" == "1" ]] || {
    printf 'skip: import pipeline profile\n'
    return 0
  }

  local source_name source_ext drop_name
  source_name="$(basename "$VIDEO_PATH")"
  source_ext="${source_name##*.}"
  if [[ "$source_ext" == "$source_name" ]]; then
    source_ext="mp4"
  fi
  drop_name="${IMPORT_DROP_NAME:-profile-${RUN_ID}.${source_ext}}"

  local center_args=()
  local processor_args=()
  if [[ -n "$CENTER_NAME" ]]; then
    center_args=(--center-name "$CENTER_NAME")
  fi
  if [[ -n "$PROCESSOR_NAME" ]]; then
    processor_args=(--processor-name "$PROCESSOR_NAME")
  fi

  run_profiled_manage import_pipeline \
    kcache_video_import "$VIDEO_PATH" \
    "${center_args[@]}" \
    "${processor_args[@]}" \
    --drop-name "$drop_name" \
    --apply \
    --json

  assert_import_pipeline_anonymized
}

run_video_streaming_profile() {
  [[ "$RUN_VIDEO_STREAMING" == "1" ]] || {
    printf 'skip: video streaming profile\n'
    return 0
  }

  if [[ "$RUN_HLS_MATERIALIZATION_FOR_STREAMING" == "1" && "$VIDEO_STREAMING_PROFILE_ENDPOINT" != "mp4" ]]; then
    run_manage materialize_hls_for_streaming_profile \
      materialize_video_hls \
      --artifact-kind processed \
      --limit "$VIDEO_STREAMING_PROFILE_LIMIT" \
      --apply \
      --inline \
      --json
  fi

  run_profiled_manage video_streaming \
    profile_video_streaming \
    --endpoint "$VIDEO_STREAMING_PROFILE_ENDPOINT" \
    --iterations "$VIDEO_STREAMING_PROFILE_ITERATIONS" \
    --limit "$VIDEO_STREAMING_PROFILE_LIMIT" \
    --json
}

run_segment_updates_profile() {
  [[ "$RUN_SEGMENT_UPDATES" == "1" ]] || {
    printf 'skip: segment updates profile\n'
    return 0
  }

  run_profiled_manage segment_updates \
    profile_segment_updates \
    --operation both \
    --segments "$SEGMENT_PROFILE_SEGMENTS" \
    --create-count "$SEGMENT_PROFILE_CREATE_COUNT" \
    --update-count "$SEGMENT_PROFILE_UPDATE_COUNT" \
    --delete-count "$SEGMENT_PROFILE_DELETE_COUNT" \
    --frame-count "$SEGMENT_PROFILE_FRAME_COUNT" \
    --removed-frame-step "$SEGMENT_PROFILE_REMOVED_FRAME_STEP" \
    --json
}

run_ensure_segment_annotations_profile() {
  [[ "$RUN_ENSURE_SEGMENT_ANNOTATIONS" == "1" ]] || {
    printf 'skip: ensure segment annotations profile\n'
    return 0
  }

  run_profiled_manage ensure_segment_annotations \
    ensure_segment_annotations \
    --all-videos \
    --dry-run
}

run_reconcile_frame_segments_profile() {
  [[ "$RUN_RECONCILE_FRAME_SEGMENTS" == "1" ]] || {
    printf 'skip: frame/segment reconciliation profile\n'
    return 0
  }

  run_profiled_manage reconcile_frame_segment_annotations \
    reconcile_frame_segment_annotations \
    --json
}

main() {
  parse_args "$@"
  ensure_runtime
  clean_profiles_folder
  printf 'profile directory: %s\n' "$PROFILE_DIR"
  printf 'settings module: %s\n' "${DJANGO_SETTINGS_MODULE:-endoreg_db.config.settings.dev}"
  printf 'python: %s\n' "$PYTHON"

  run_setup_steps

  local runner
  for runner in "${PROFILE_RUNNERS[@]}"; do
    "$runner"
  done

  printf '\nProfiling suite complete.\n'
  printf 'Artifacts: %s\n' "$PROFILE_DIR"
}

main "$@"
