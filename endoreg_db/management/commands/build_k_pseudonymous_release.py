from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import TypedDict, Unpack, cast

import yaml
from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.schemas.k_pseudonymity import KPseudonymityReleaseConfig
from endoreg_db.services.k_pseudonymity import (
    KPseudonymityInputError,
    ReleaseRow,
    build_k_pseudonymous_release,
)
from endoreg_db.utils.file_operations import atomic_write_file, safe_unlink_file


class CommandOptions(TypedDict):
    config: str
    input_csv: str
    release_output: str
    audit_output: str


class Command(BaseCommand):
    help = (
        "Build a bounded (k, l, t)-pseudonymous secondary-use CSV view. "
        "No release file is retained unless the declared predicate is satisfied."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("config", help="YAML release-policy configuration.")
        parser.add_argument("input_csv", help="De-identified source table in CSV form.")
        parser.add_argument(
            "--release-output",
            required=True,
            help="Destination for the governed recipient-visible CSV view.",
        )
        parser.add_argument(
            "--audit-output",
            required=True,
            help="Destination for the protected custodian audit manifest.",
        )

    def handle(self, *args: str, **options: Unpack[CommandOptions]) -> None:
        config_path = Path(options["config"]).expanduser().resolve()
        input_path = Path(options["input_csv"]).expanduser().resolve()
        release_path = Path(options["release_output"]).expanduser().resolve()
        audit_path = Path(options["audit_output"]).expanduser().resolve()
        if release_path == audit_path:
            raise CommandError("release and audit outputs must be different files")

        config = _load_config(config_path)
        rows = _read_csv(input_path, max_rows=config.max_input_rows)
        try:
            result = build_k_pseudonymous_release(rows, config)
        except KPseudonymityInputError as exc:
            raise CommandError(str(exc)) from exc

        audit_bytes = result.manifest.model_dump_json(indent=2).encode("utf-8")
        atomic_write_file(
            destination=audit_path,
            content=(audit_bytes,),
            required_bytes=len(audit_bytes),
            file_mode=0o600,
            dir_mode=0o700,
        )

        if result.released_rows is None:
            safe_unlink_file(release_path, missing_ok=True)
            raise CommandError(
                "Release predicate not satisfied; no release CSV was retained. "
                f"reason={result.manifest.reason}; audit={audit_path}"
            )

        release_bytes = _encode_csv(
            result.released_rows,
            fieldnames=config.release_columns,
        )
        atomic_write_file(
            destination=release_path,
            content=(release_bytes,),
            required_bytes=len(release_bytes),
            file_mode=0o600,
            dir_mode=0o700,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Release predicate satisfied; "
                f"rows={len(result.released_rows)} "
                f"synthetic_rows={result.manifest.synthetic_row_count} "
                f"release={release_path} audit={audit_path}"
            )
        )


def _load_config(path: Path) -> KPseudonymityReleaseConfig:
    if not path.is_file():
        raise CommandError(f"configuration file does not exist: {path}")
    try:
        payload: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CommandError(f"failed to read YAML configuration: {exc}") from exc
    try:
        return KPseudonymityReleaseConfig.model_validate(payload)
    except ValidationError as exc:
        raise CommandError(f"invalid release configuration: {exc}") from exc


def _read_csv(path: Path, *, max_rows: int) -> tuple[dict[str, object], ...]:
    if not path.is_file():
        raise CommandError(f"input CSV does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            if not fieldnames:
                raise CommandError("input CSV must contain a header row")
            if len(fieldnames) != len(set(fieldnames)):
                raise CommandError("input CSV header contains duplicate columns")
            rows: list[dict[str, object]] = []
            for row_number, raw_row in enumerate(reader, start=1):
                if row_number > max_rows:
                    raise CommandError(f"input CSV exceeds max_input_rows={max_rows}")
                if None in raw_row:
                    raise CommandError(
                        f"input CSV row {row_number} contains more values than headers"
                    )
                rows.append(
                    {
                        key: value
                        for key, value in cast(dict[str, str | None], raw_row).items()
                    }
                )
    except (OSError, csv.Error) as exc:
        raise CommandError(f"failed to read input CSV: {exc}") from exc
    if not rows:
        raise CommandError("input CSV must contain at least one data row")
    return tuple(rows)


def _encode_csv(rows: tuple[ReleaseRow, ...], *, fieldnames: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(fieldnames),
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")
