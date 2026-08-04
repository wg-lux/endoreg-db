from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import Literal, TypeAlias

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.services.anonymization_quality_evaluation import (
    SensitiveMetaHandlingPolicy,
    evaluate_anonymization_quality,
    parse_quality_datetime,
)
from endoreg_db.utils.file_operations import atomic_write_file


QualityMediaType: TypeAlias = Literal["all", "video", "pdf"]
QUALITY_MEDIA_TYPE_CHOICES: tuple[QualityMediaType, ...] = ("all", "video", "pdf")


class Command(BaseCommand):
    help = (
        "Evaluate anonymization quality for already-imported media and persist "
        "derived-only quality metrics."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--media-type",
            choices=QUALITY_MEDIA_TYPE_CHOICES,
            default="all",
            help="Evaluate videos, reports, or both.",
        )
        parser.add_argument(
            "--video-id",
            action="append",
            type=int,
            default=[],
            help="VideoFile ID to evaluate. Can be passed more than once.",
        )
        parser.add_argument(
            "--pdf-id",
            action="append",
            type=int,
            default=[],
            help="RawPdfFile ID to evaluate. Can be passed more than once.",
        )
        parser.add_argument(
            "--center-id",
            type=int,
            default=None,
            help="Restrict evaluation to one center ID.",
        )
        parser.add_argument(
            "--date-from",
            default="",
            help="Lower upload/import timestamp bound. Accepts ISO date or datetime.",
        )
        parser.add_argument(
            "--date-to",
            default="",
            help="Upper upload/import timestamp bound. Accepts ISO date or datetime.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of media rows to evaluate. 0 means no limit.",
        )
        parser.add_argument(
            "--include-unvalidated",
            action="store_true",
            help="Include unvalidated media and report them as not_validated.",
        )
        parser.add_argument(
            "--sensitive-meta-policy",
            choices=[policy.value for policy in SensitiveMetaHandlingPolicy],
            default=SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS.value,
            help=(
                "Governed SensitiveMeta handling policy. The default is "
                "clear_direct_identifiers."
            ),
        )
        parser.add_argument(
            "--apply-policy",
            action="store_true",
            help=(
                "Apply the selected SensitiveMeta policy. Without this flag the "
                "command audits and records derived metrics only."
            ),
        )
        parser.add_argument(
            "--allow-sensitive-meta-delete",
            action="store_true",
            help=(
                "Allow delete_sensitive_meta to delete an unreferenced SensitiveMeta "
                "row. Ignored for other policies."
            ),
        )
        parser.add_argument(
            "--json-output",
            default="",
            help="Write the full JSON payload to this path using atomic write semantics.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the full JSON payload to stdout.",
        )

    def handle(self, *args, **options) -> None:
        limit = int(options["limit"])
        if limit < 0:
            raise CommandError("--limit must be >= 0")

        video_ids = tuple(int(value) for value in options["video_id"])
        pdf_ids = tuple(int(value) for value in options["pdf_id"])
        media_type = _parse_media_type(options["media_type"])
        if media_type == "video" and pdf_ids:
            raise CommandError("--pdf-id cannot be used with --media-type=video")
        if media_type == "pdf" and video_ids:
            raise CommandError("--video-id cannot be used with --media-type=pdf")

        try:
            date_from = parse_quality_datetime(options["date_from"])
            date_to = parse_quality_datetime(options["date_to"], end_of_day=True)
            policy = SensitiveMetaHandlingPolicy(options["sensitive_meta_policy"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        payload = evaluate_anonymization_quality(
            media_type=media_type,
            video_ids=video_ids,
            pdf_ids=pdf_ids,
            center_id=options["center_id"],
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            include_unvalidated=bool(options["include_unvalidated"]),
            sensitive_meta_policy=policy,
            apply_policy=bool(options["apply_policy"]),
            allow_sensitive_meta_delete=bool(options["allow_sensitive_meta_delete"]),
        )
        serialized = payload.model_dump(mode="json")

        if options["json_output"]:
            _write_json_output(Path(options["json_output"]), serialized)

        if options["json"]:
            self.stdout.write(json.dumps(serialized, indent=2, sort_keys=True))
        else:
            summary = serialized["summary"]
            self.stdout.write(
                "Evaluated {total} media rows; residual_phi_detected={residual}; "
                "leaked_fields={leaked}; raw_artifact_residuals={raw}; "
                "missing_sensitive_meta_deletions={missing}".format(
                    total=summary["total"],
                    residual=summary["residual_phi_detected_count"],
                    leaked=summary["leaked_field_count"],
                    raw=summary["raw_artifact_residual_count"],
                    missing=summary["missing_sensitive_meta_deletion_count"],
                )
            )


def _write_json_output(path: Path, payload: dict) -> None:
    json_bytes = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    atomic_write_file(
        destination=path,
        content=[json_bytes],
        required_bytes=len(json_bytes),
    )


def _parse_media_type(value: object) -> QualityMediaType:
    media_type = str(value)
    if media_type == "all":
        return "all"
    if media_type == "video":
        return "video"
    if media_type == "pdf":
        return "pdf"
    raise CommandError("--media-type must be one of: all, video, pdf")
