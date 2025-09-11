# endoreg_db/codemods/rename_datetime_fields.py
from bowler import Query
from pathlib import Path
import argparse, yaml, sys

# Paths
BASE = Path(__file__).resolve().parents[1]  # .../endoreg_db
RENAMES_YML = BASE / "renames.yml"
DEFAULT_TARGETS = ["endoreg_db/models"]  # safer default
EXCLUDE_DIR_NAMES = {"migrations", "__pycache__"}

def load_renames():
    if not RENAMES_YML.exists():
        print(f"ERROR: renames.yml not found at {RENAMES_YML}", file=sys.stderr)
        sys.exit(2)
    data = yaml.safe_load(RENAMES_YML.read_text()) or {}
    if not isinstance(data, dict) or not data:
        print("ERROR: renames.yml is empty or not a mapping.", file=sys.stderr)
        sys.exit(2)
    return data

def iter_python_targets(paths):
    """Yield *.py files under given paths, excluding migrations and caches."""
    for p in map(Path, paths):
        if p.is_file() and p.suffix == ".py":
            if not any(part in EXCLUDE_DIR_NAMES for part in p.parts):
                yield str(p)
        elif p.is_dir():
            for f in p.rglob("*.py"):
                if any(part in EXCLUDE_DIR_NAMES for part in f.parts):
                    continue
                yield str(f)

def build_query(files):
    # Bowler can take a list of files; we’ve already filtered them
    return Query(list(files))

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Rename legacy datetime fields to standardized names."
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Files/dirs to process. Default: endoreg_db/models",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply changes (write). Omit for a dry run.",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Reduce output verbosity.",
    )
    args = parser.parse_args(argv)

    targets = args.targets or DEFAULT_TARGETS
    if args.targets == []:
        print(
            "NOTICE: Using default target 'endoreg_db/models'. "
            "Pass explicit paths to broaden scope.",
            file=sys.stderr,
        )

    files = list(iter_python_targets(targets))
    if not files:
        print("No Python files found to process.", file=sys.stderr)
        return 0

    renames = load_renames()
    q = build_query(files)

    # Build transforms
    for old, new in renames.items():
        # obj.date_created  -> obj.created_at
        q.select_attribute(old).rename(new)
        # LHS or bare names: date_created = models.DateTimeField(...)
        q.select_var(old).rename(new)

    # Execute (dry-run by default)
    q.execute(write=args.yes, silent=args.silent)
    if not args.yes:
        print(
            "\nDry run complete. Re-run with --yes to apply changes.",
            file=sys.stderr,
        )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
