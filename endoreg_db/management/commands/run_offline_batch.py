from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.offline_batch_runner import (
    OfflineBatchAlreadyRunning,
    OfflineBatchConfigurationError,
    OfflineBatchExecutionError,
    OfflineBatchInterrupted,
    OfflineBatchRunnerError,
    OfflineBatchRuntimeExceeded,
    load_offline_batch_runner_config,
    run_offline_batch,
)


class Command(BaseCommand):
    help = (
        "Run the optional Snakemake offline batch lane under a fail-closed "
        "single-instance supervisor."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--config",
            type=Path,
            default=Path("config/offline_batch_runner.yaml"),
            help="Path to the typed offline batch runner YAML configuration.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit the successful runner summary as JSON.",
        )

    def handle(self, *args: object, **options: object) -> None:
        config_path = options.get("config")
        if not isinstance(config_path, Path):
            raise CommandError("Offline batch runner config path is invalid.")
        json_output = bool(options.get("json_output"))

        try:
            config = load_offline_batch_runner_config(config_path)
            result = run_offline_batch(config)
        except OfflineBatchAlreadyRunning as exc:
            raise CommandError(str(exc), returncode=75) from exc
        except OfflineBatchInterrupted as exc:
            raise CommandError(
                str(exc),
                returncode=128 + exc.signal_number,
            ) from exc
        except OfflineBatchExecutionError as exc:
            return_code = exc.return_code if 0 < exc.return_code <= 255 else 1
            raise CommandError(str(exc), returncode=return_code) from exc
        except OfflineBatchRuntimeExceeded as exc:
            raise CommandError(str(exc), returncode=124) from exc
        except (OfflineBatchConfigurationError, OfflineBatchRunnerError) as exc:
            raise CommandError(str(exc), returncode=1) from exc

        payload = {
            "schema_version": "1.0",
            "batch_id": result.batch_id,
            "supervisor_config_sha256": result.supervisor_config_sha256,
            "workflow_config_sha256": result.workflow_config_sha256,
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat(),
            "status": result.status,
            "exit_code": result.exit_code,
            "duration_seconds": result.duration_seconds,
            "failure_count": result.failure_count,
        }
        if json_output:
            self.stdout.write(json.dumps(payload, sort_keys=True))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Offline batch completed successfully "
                    f"(batch_id={result.batch_id})."
                )
            )
