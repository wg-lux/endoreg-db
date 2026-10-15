"""One explicit operator workflow for typed deployment configuration."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DatabaseError
from lx_dtypes.models.contracts.deployment_setup import DeploymentSetup
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from lx_dtypes.terminology.terminology_service import TerminologyError
from lx_dtypes.utils.deployment_setup import (
    parse_deployment_lock,
    parse_deployment_setup,
)
from lx_dtypes.utils.study_setup_yaml import MAX_SETUP_BYTES

from endoreg_db.services.runtime.deployment_setup import (
    DeploymentActivationError,
    apply_deployment,
    plan_deployment,
    validate_deployment,
)
from endoreg_db.utils.file_operations import atomic_write_file

TEMPLATES = ("minimal", "coloreg", "training", "green_endoscopy")


def _path(options: dict[str, object], name: str) -> Path:
    value = options.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} requires a filesystem path")
    return Path(value).expanduser().resolve()


def _read(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(MAX_SETUP_BYTES + 1)
    if len(raw) > MAX_SETUP_BYTES:
        raise ValueError("setup document exceeds the 1 MiB limit")
    return raw.decode("utf-8")


class Command(BaseCommand):
    help = "Generate, validate, plan and apply a locked deployment setup."

    def add_arguments(self, parser: CommandParser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)
        template = actions.add_parser("template")
        template.add_argument("name", choices=TEMPLATES)
        template.add_argument("--output")
        schema = actions.add_parser("schema")
        schema.add_argument("--output")
        actions.add_parser("provision")
        for action in ("validate", "plan", "apply"):
            command = actions.add_parser(action)
            command.add_argument("--file", required=True)
            command.add_argument("--format", choices=("json", "yml"), default="yml")
            if action in ("plan", "apply"):
                command.add_argument("--lock-file", required=action == "apply")

    def _emit(self, content: str, options: dict[str, object]) -> None:
        if options.get("output") is None:
            self.stdout.write(content)
        else:
            destination = _path(options, "output")
            if destination.exists():
                raise ValueError("output already exists; choose a new path")
            atomic_write_file(
                destination=destination,
                content=[content.encode("utf-8")],
                file_mode=0o600,
            )
            self.stdout.write(f"Written: {destination}")

    def handle(self, *args: str, **options: object) -> None:
        try:
            action = options["action"]
            if action == "template":
                name = options["name"]
                if not isinstance(name, str) or name not in TEMPLATES:
                    raise ValueError("unknown template")
                self._emit(
                    files("lx_dtypes")
                    .joinpath("setup_templates", f"{name}.yml")
                    .read_text(encoding="utf-8"),
                    options,
                )
                return
            if action == "schema":
                self._emit(
                    json.dumps(DeploymentSetup.model_json_schema(), indent=2) + "\n",
                    options,
                )
                return
            service = get_terminology_service()
            if action == "provision":
                service.provision()
                self.stdout.write(
                    "Shipped registry provisioned; existing custom entries preserved."
                )
                return
            source = _path(options, "file")
            setup = parse_deployment_setup(_read(source))
            if action == "apply":
                expected = parse_deployment_lock(_read(_path(options, "lock_file")))
                result = apply_deployment(setup, expected, service)
                payload = {"applied": True, "plan": result.model_dump(mode="json")}
            else:
                resolved = validate_deployment(setup, service)
                if action == "validate":
                    payload = {
                        "valid": True,
                        "lock": resolved.lock.model_dump(mode="json"),
                    }
                elif action == "plan":
                    plan = plan_deployment(resolved)
                    payload = plan.model_dump(mode="json")
                    if options.get("lock_file") is not None:
                        destination = _path(options, "lock_file")
                        if destination == source:
                            raise ValueError("lock file must differ from the manifest")
                        if not plan.can_apply:
                            self.stdout.write(
                                yaml.safe_dump(
                                    payload, sort_keys=False, allow_unicode=True
                                )
                            )
                            raise CommandError(
                                "Deployment conflicts; no lock file written"
                            )
                        atomic_write_file(
                            destination=destination,
                            content=[
                                yaml.safe_dump(
                                    resolved.lock.model_dump(mode="json"),
                                    sort_keys=False,
                                    allow_unicode=True,
                                ).encode()
                            ],
                            file_mode=0o600,
                        )
                else:
                    raise ValueError("unknown setup action")
            self.stdout.write(
                json.dumps(payload, indent=2)
                if options["format"] == "json"
                else yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
            )
            if action == "plan" and not payload["can_apply"]:
                raise CommandError(
                    "Deployment conflicts; no database or registry changes made"
                )
        except DeploymentActivationError as exc:
            raise CommandError(str(exc)) from exc
        except (ValueError, OSError, TerminologyError, DatabaseError) as exc:
            raise CommandError(f"Deployment setup failed: {exc}") from exc
