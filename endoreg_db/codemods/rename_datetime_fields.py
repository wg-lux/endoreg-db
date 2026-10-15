from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import NoReturn, Protocol, Self, cast

import yaml
from pydantic import ValidationError

from lx_dtypes.models.contracts import validate_codemod_rename_map
from lx_dtypes.models.contracts.json_types import JsonValue

# Paths
BASE = Path(__file__).resolve().parents[1]  # .../endoreg_db
RENAMES_YML = BASE / "renames.yml"
DEFAULT_TARGETS = ("endoreg_db/models",)  # safer default
EXCLUDE_DIR_NAMES = {"migrations", "__pycache__"}

type CliArgList = Sequence[str] | None
type PathInput = str | Path


class BowlerQuery(Protocol):
    def select_attribute(self, name: str) -> Self: ...

    def select_var(self, name: str) -> Self: ...

    def rename(self, new_name: str) -> Self: ...

    def execute(self, *, write: bool, silent: bool) -> None: ...


class BowlerQueryFactory(Protocol):
    def __call__(self, filenames: list[str]) -> BowlerQuery: ...


class ParsedArguments(Protocol):
    targets: list[str]
    yes: bool
    silent: bool


def _exit_config_error(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def load_renames() -> dict[str, str]:
    if not RENAMES_YML.exists():
        _exit_config_error(f"ERROR: renames.yml not found at {RENAMES_YML}")

    payload = cast(JsonValue, yaml.safe_load(RENAMES_YML.read_text(encoding="utf-8")))
    try:
        return validate_codemod_rename_map(payload).renames
    except ValidationError as exc:
        _exit_config_error(f"ERROR: invalid renames.yml: {exc}")


def iter_python_targets(paths: Iterable[PathInput]) -> Iterator[str]:
    """Yield *.py files under given paths, excluding migrations and caches."""
    for path in map(Path, paths):
        if path.is_file() and path.suffix == ".py":
            if not any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                yield str(path)
        elif path.is_dir():
            for file_path in path.rglob("*.py"):
                if any(part in EXCLUDE_DIR_NAMES for part in file_path.parts):
                    continue
                yield str(file_path)


def build_query(files: Iterable[str]) -> BowlerQuery:
    # Bowler can take a list of files; we’ve already filtered them
    bowler_mod = importlib.import_module("bowler")
    query_cls = cast(BowlerQueryFactory, getattr(bowler_mod, "Query"))
    return query_cls(list(files))


def main(argv: CliArgList = None) -> int:
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
    args = cast(ParsedArguments, parser.parse_args(argv))

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
