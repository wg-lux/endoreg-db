from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeAlias

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.anonymization_quality_evaluation import (
    AnonymizationQualityPayload,
    SensitiveMetaHandlingPolicy,
    evaluate_anonymization_quality,
    parse_quality_datetime,
)
from endoreg_db.services.evaluation_manifest import write_quality_evaluation_manifest
from endoreg_db.utils.file_operations import atomic_write_file


QualityMediaType: TypeAlias = Literal["all", "video", "pdf"]
QUALITY_MEDIA_TYPE_CHOICES: tuple[QualityMediaType, ...] = ("all", "video", "pdf")
type _CommandOption = None | bool | int | list[int] | str
type _MaybeInt = None | int


@dataclass(frozen=True)
class _EvaluationRequest:
    media_type: QualityMediaType
    video_ids: tuple[int, ...]
    pdf_ids: tuple[int, ...]
    center_id: int | None
    date_from: datetime | None
    date_to: datetime | None
    limit: int
    include_unvalidated: bool
    sensitive_meta_policy: SensitiveMetaHandlingPolicy
    apply_policy: bool
    allow_sensitive_meta_delete: bool
    json_output_path: Path | None
    generate_manifest: bool
    manifest_output_dir: Path | None
    emit_json: bool


class Command(BaseCommand):
    help = (
        "Evaluate anonymization quality for already-imported media and persist "
        "derived-only quality metrics."
    )

    def add_arguments(self, parser: CommandParser) -> None:
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
            "--generate-manifest",
            action="store_true",
            help=(
                "Write a PHI-safe unified evaluation manifest for this run to "
                "/data/results/manifests by default."
            ),
        )
        parser.add_argument(
            "--manifest-output-dir",
            default="",
            help=(
                "Override the evaluation manifest output directory. Intended for "
                "controlled test or export environments."
            ),
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the full JSON payload to stdout.",
        )

    def handle(self, *args: str, **options: _CommandOption) -> None:
        _ = args
        request = _evaluation_request_from_options(options)
        payload = _evaluate_request(request)
        self._write_optional_artifacts(payload, request=request)
        self._write_payload(payload, emit_json=request.emit_json)

    def _write_optional_artifacts(
        self,
        payload: AnonymizationQualityPayload,
        *,
        request: _EvaluationRequest,
    ) -> None:
        if request.json_output_path is not None:
            _write_json_output(request.json_output_path, payload)
        if request.generate_manifest:
            manifest_path = write_quality_evaluation_manifest(
                payload,
                output_dir=request.manifest_output_dir,
            )
            self.stderr.write(f"evaluation_manifest: {manifest_path}")

    def _write_payload(
        self,
        payload: AnonymizationQualityPayload,
        *,
        emit_json: bool,
    ) -> None:
        if emit_json:
            self.stdout.write(payload.model_dump_json(indent=2))
            return
        summary = payload.summary
        self.stdout.write(
            "Evaluated {total} media rows; residual_phi_detected={residual}; "
            "leaked_fields={leaked}; raw_artifact_residuals={raw}; "
            "missing_sensitive_meta_deletions={missing}".format(
                total=summary.total,
                residual=summary.residual_phi_detected_count,
                leaked=summary.leaked_field_count,
                raw=summary.raw_artifact_residual_count,
                missing=summary.missing_sensitive_meta_deletion_count,
            )
        )


def _evaluation_request_from_options(
    options: dict[str, _CommandOption],
) -> _EvaluationRequest:
    limit = _int_option(options, "limit")
    if limit < 0:
        raise CommandError("--limit must be >= 0")

    video_ids = tuple(_int_sequence_option(options, "video_id"))
    pdf_ids = tuple(_int_sequence_option(options, "pdf_id"))
    media_type = _parse_media_type(_string_option(options, "media_type"))
    _validate_media_id_filters(
        media_type=media_type,
        video_ids=video_ids,
        pdf_ids=pdf_ids,
    )

    try:
        date_from = parse_quality_datetime(_string_option(options, "date_from"))
        date_to = parse_quality_datetime(
            _string_option(options, "date_to"), end_of_day=True
        )
        policy = SensitiveMetaHandlingPolicy(
            _string_option(options, "sensitive_meta_policy")
        )
    except ValueError as exc:
        raise CommandError(str(exc)) from exc

    return _EvaluationRequest(
        media_type=media_type,
        video_ids=video_ids,
        pdf_ids=pdf_ids,
        center_id=_maybe_int_option(options, "center_id"),
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        include_unvalidated=_bool_option(options, "include_unvalidated"),
        sensitive_meta_policy=policy,
        apply_policy=_bool_option(options, "apply_policy"),
        allow_sensitive_meta_delete=_bool_option(
            options, "allow_sensitive_meta_delete"
        ),
        json_output_path=_optional_path_option(options, "json_output"),
        generate_manifest=_bool_option(options, "generate_manifest"),
        manifest_output_dir=_optional_path_option(options, "manifest_output_dir"),
        emit_json=_bool_option(options, "json"),
    )


def _validate_media_id_filters(
    *,
    media_type: QualityMediaType,
    video_ids: tuple[int, ...],
    pdf_ids: tuple[int, ...],
) -> None:
    if media_type == "video" and pdf_ids:
        raise CommandError("--pdf-id cannot be used with --media-type=video")
    if media_type == "pdf" and video_ids:
        raise CommandError("--video-id cannot be used with --media-type=pdf")


def _optional_path_option(
    options: dict[str, _CommandOption],
    name: str,
) -> Path | None:
    value = _string_option(options, name)
    return Path(value) if value else None


def _evaluate_request(request: _EvaluationRequest) -> AnonymizationQualityPayload:
    return evaluate_anonymization_quality(
        media_type=request.media_type,
        video_ids=request.video_ids,
        pdf_ids=request.pdf_ids,
        center_id=request.center_id,
        date_from=request.date_from,
        date_to=request.date_to,
        limit=request.limit,
        include_unvalidated=request.include_unvalidated,
        sensitive_meta_policy=request.sensitive_meta_policy,
        apply_policy=request.apply_policy,
        allow_sensitive_meta_delete=request.allow_sensitive_meta_delete,
    )


def _write_json_output(path: Path, payload: AnonymizationQualityPayload) -> None:
    json_bytes = payload.model_dump_json(indent=2).encode("utf-8")
    atomic_write_file(
        destination=path,
        content=[json_bytes],
        required_bytes=len(json_bytes),
    )


def _parse_media_type(value: str) -> QualityMediaType:
    media_type = value
    if media_type == "all":
        return "all"
    if media_type == "video":
        return "video"
    if media_type == "pdf":
        return "pdf"
    raise CommandError("--media-type must be one of: all, video, pdf")


def _string_option(options: dict[str, _CommandOption], name: str) -> str:
    value = options[name]
    if not isinstance(value, str):
        raise CommandError(f"--{name.replace('_', '-')} must be a string")
    return value


def _int_option(options: dict[str, _CommandOption], name: str) -> int:
    value = options[name]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise CommandError(f"--{name.replace('_', '-')} must be an integer")


def _maybe_int_option(options: dict[str, _CommandOption], name: str) -> _MaybeInt:
    value = options[name]
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise CommandError(f"--{name.replace('_', '-')} must be an integer")


def _bool_option(options: dict[str, _CommandOption], name: str) -> bool:
    value = options[name]
    if isinstance(value, bool):
        return value
    raise CommandError(f"--{name.replace('_', '-')} must be a boolean flag")


def _int_sequence_option(options: dict[str, _CommandOption], name: str) -> list[int]:
    value = options[name]
    if not isinstance(value, list):
        raise CommandError(f"--{name.replace('_', '-')} must be an integer list")
    return value
