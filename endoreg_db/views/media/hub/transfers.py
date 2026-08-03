from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.serializers.hub import (
    TransferJobCreateSerializer,
    TransferJobStatusSerializer,
)
from endoreg_db.services.hub import (
    apply_transfer_metadata,
    attach_transfer_media,
    authenticate_network_node,
    create_or_reuse_transfer_job,
    transfer_api_enabled,
)
from endoreg_db.services.hub.transfer_logging import (
    decision,
    error,
    info,
    kv,
    section,
    step,
    success,
    transfer_summary,
    warning,
)
from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    hash_identifier,
)
from lx_dtypes.models.contracts.json_types import JsonValue
from lx_dtypes.models.contracts.transfer_validation import (
    TransferValidationFailureLogPayload,
)

if TYPE_CHECKING:
    from endoreg_db.services.hub.transfers import TransferProvenance

logger = logging.getLogger(__name__)

_ValidationErrorValue: TypeAlias = str | list[str] | dict[str, "_ValidationErrorValue"]

_TransferPayloadValue: TypeAlias = (
    str
    | int
    | bool
    | None
    | list["_TransferPayloadValue"]
    | dict[str, "_TransferPayloadValue"]
)

_StructuredLogValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["_StructuredLogValue"]
    | dict[str, "_StructuredLogValue"]
)


class _SerializerLike(Protocol):
    @property
    def data(self) -> Mapping[str, _TransferPayloadValue]: ...


class _SerializerErrorLike(Protocol):
    @property
    def errors(self) -> _ValidationErrorValue: ...


def _assert_transfer_api_enabled() -> None:
    if not transfer_api_enabled():
        error("Hub transfer API is not enabled on this deployment")
        raise Http404("Hub transfer API is not enabled")
    success("Hub transfer API is enabled")


def _node_header(request: Request, header_name: str) -> str:
    return str(request.headers.get(header_name, "") or "").strip()


def _safe_request_context(request: Request) -> dict[str, str | None]:
    remote_addr = str(request.META.get("REMOTE_ADDR", "") or "").strip()
    return {
        "request_method": str(getattr(request, "method", "") or ""),
        "remote_addr_sha256": hash_identifier(remote_addr) if remote_addr else None,
    }


def _serialize_response_data(serializer: object) -> Mapping[str, _TransferPayloadValue]:
    return cast(_SerializerLike, serializer).data


def _serialize_validation_error_payload(serializer: object) -> _ValidationErrorValue:
    return cast(_SerializerErrorLike, serializer).errors


def _django_validation_details(
    exc: DjangoValidationError,
) -> dict[str, list[str]]:
    """Normalize model validation failures without logging submitted values."""
    if hasattr(exc, "error_dict"):
        return {
            str(field_name): [str(message) for message in messages]
            for field_name, messages in exc.message_dict.items()
        }
    return {"non_field_errors": [str(message) for message in exc.messages]}


def _validation_error_fields(
    errors: _ValidationErrorValue,
    prefix: str = "",
) -> list[str]:
    if isinstance(errors, dict):
        fields: list[str] = []
        for key, value in errors.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.extend(_validation_error_fields(value, path))
        return fields
    if isinstance(errors, list):
        fields = []
        for item in errors:
            if isinstance(item, (dict, list)):
                fields.extend(_validation_error_fields(item, prefix))
        return fields or [prefix or "non_field_errors"]
    return [prefix or "non_field_errors"]


def _log_transfer_validation_failure(
    request: Request,
    *,
    event: str,
    errors: _ValidationErrorValue,
    transfer_key: str | None = None,
    transfer_job: TransferJob | None = None,
) -> None:
    error_fields = sorted(set(_validation_error_fields(errors)))
    payload = _safe_request_context(request)
    if transfer_key:
        payload["transfer_key_sha256"] = hash_identifier(transfer_key)
    if transfer_job is not None:
        payload["transfer_job_id"] = str(transfer_job.pk)
        payload["resource_kind"] = str(getattr(transfer_job, "resource_kind", ""))
    structured_payload = TransferValidationFailureLogPayload(
        error_fields=error_fields,
        request_method=str(payload["request_method"] or ""),
        remote_addr_sha256=payload["remote_addr_sha256"],
        transfer_key_sha256=payload.get("transfer_key_sha256"),
        transfer_job_id=payload.get("transfer_job_id"),
        resource_kind=payload.get("resource_kind"),
    )
    emit_structured_event(
        logger,
        event,
        level=logging.WARNING,
        error_fields=cast(list[_StructuredLogValue], structured_payload.error_fields),
        request_method=structured_payload.request_method,
        remote_addr_sha256=structured_payload.remote_addr_sha256,
        transfer_key_sha256=structured_payload.transfer_key_sha256,
        transfer_job_id=structured_payload.transfer_job_id,
        resource_kind=structured_payload.resource_kind,
    )


def _assert_secure_transfer_transport(request: Request) -> None:
    secure_transport_required = bool(
        getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT", True)
    )
    kv("Secure transport required", secure_transport_required)
    kv("Request is secure", request.is_secure())
    if not secure_transport_required:
        warning("Secure transport enforcement is disabled")
        return
    if request.is_secure():
        success("Secure transport requirement passed")
        return
    emit_structured_event(
        logger,
        "hub.transfer_secure_transport_failed",
        level=logging.WARNING,
        reason="insecure_request",
        **_safe_request_context(request),
    )
    error("Transfer rejected because secure transport is required")
    raise PermissionDenied("Hub transfer requires HTTPS or equivalent secure transport")


def _assert_transfer_mtls(request: Request) -> None:
    mtls_required = bool(getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS", False))
    kv("Mutual TLS required", mtls_required)
    if not mtls_required:
        info("Mutual TLS verification is disabled for this receiver process")
        return
    meta_key = str(
        getattr(settings, "ENDOREG_HUB_TRANSFER_MTLS_META_KEY", "") or ""
    ).strip()
    expected_value = str(
        getattr(settings, "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE", "") or ""
    ).strip()
    actual_value = str(request.META.get(meta_key, "") or "").strip()
    kv("Mutual TLS metadata key configured", bool(meta_key))
    kv("Mutual TLS expected value configured", bool(expected_value))
    kv("Mutual TLS verification value present", bool(actual_value))
    if not meta_key or not expected_value or actual_value != expected_value:
        reason = (
            "mtls_proxy_metadata_not_configured"
            if not meta_key or not expected_value
            else "mtls_proxy_verification_failed"
        )
        emit_structured_event(
            logger,
            "hub.transfer_mtls_check_failed",
            level=logging.WARNING,
            reason=reason,
            mtls_meta_key_configured=bool(meta_key),
            mtls_expected_value_configured=bool(expected_value),
            mtls_actual_value_present=bool(actual_value),
            **_safe_request_context(request),
        )
        error("Mutual TLS verification failed")
        raise PermissionDenied(
            "Hub transfer requires proxy-verified mutual TLS client authentication"
        )
    success("Proxy-verified mutual TLS requirement passed")


def _enforce_transfer_node_auth(request: Request, source_node_key: str) -> NetworkNode:
    step("AUTH-1", "Validate transfer transport")
    _assert_secure_transfer_transport(request)
    _assert_transfer_mtls(request)
    step("AUTH-2", "Authenticate source network node")
    provided_node_key = _node_header(request, "X-Network-Node-Key")
    provided_secret = _node_header(request, "X-Network-Node-Secret")
    authenticated_node = authenticate_network_node(
        source_node_key=source_node_key,
        provided_node_key=provided_node_key,
        provided_secret=provided_secret,
    )
    if authenticated_node is None:
        error("Network-node authentication failed")
        raise PermissionDenied("Invalid network node credentials for this transfer")
    kv("Authenticated node key", authenticated_node.node_key)
    success("Network-node authentication passed")
    return authenticated_node


def _assert_transfer_center_scope(
    authenticated_node: NetworkNode, source_center_id: int | None
) -> None:
    """Bind node-authenticated transfers to the node's configured owner."""
    owning_center_id = getattr(authenticated_node, "owning_center_id", None)
    if (
        not isinstance(owning_center_id, int)
        or source_center_id is None
        or source_center_id != owning_center_id
    ):
        raise PermissionDenied(
            "Transfer source is outside the authenticated node scope"
        )


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferCreateView(APIView):
    authentication_classes: Sequence[type[BaseAuthentication]] = ()
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        section("RECEIVER: CREATE TRANSFER", "📥")
        kv("Request method", request.method)
        kv("Request is secure", request.is_secure())
        step(1, "Verify hub transfer API availability")
        _assert_transfer_api_enabled()
        step(2, "Validate typed transfer payload")
        serializer = TransferJobCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        if not serializer.is_valid():
            error("Transfer request failed typed serializer validation")
            validation_errors = _serialize_validation_error_payload(serializer)
            _log_transfer_validation_failure(
                request,
                event="hub.transfer_create_validation_failed",
                errors=validation_errors,
            )
            raise ValidationError(validation_errors)

        data = cast(dict[str, _TransferPayloadValue], serializer.validated_data)
        source_node = cast("NetworkNode", data.get("source_node"))
        source_node_key = cast(str, getattr(source_node, "node_key", ""))
        step(3, "Authenticate source node and center ownership")
        authenticated_node = _enforce_transfer_node_auth(request, source_node_key)
        source_center = cast(Center | None, data.get("source_center"))
        source_center_id = cast(int | None, getattr(source_center, "id", None))
        _assert_transfer_center_scope(authenticated_node, source_center_id)
        target_node = cast("NetworkNode", data.get("target_node"))
        provenance = cast("TransferProvenance", data.get("provenance", {}))
        transfer_summary(
            transfer_key=cast(str, data["transfer_key"]),
            resource_kind=cast(str, data["resource_kind"]),
            source_node_key=source_node_key,
            target_node_key=cast(str, getattr(target_node, "node_key", "")),
            resource_hash=cast(str, data["resource_hash"]),
            transfer_mode=cast(str, data["transfer_mode"]),
        )

        phase = "registration"
        step(4, "Create or reuse transfer and apply metadata atomically")
        try:
            with transaction.atomic():
                transfer_job, created = create_or_reuse_transfer_job(
                    transfer_key=cast(str, data["transfer_key"]),
                    source_node=source_node,
                    target_node=target_node,
                    source_center=source_center,
                    resource_kind=cast(str, data["resource_kind"]),
                    resource_hash=cast(str, data["resource_hash"]),
                    transfer_mode=cast(
                        str,
                        data["transfer_mode"],
                    ),
                    processing_policy=cast(
                        str,
                        data["processing_policy"],
                    ),
                    processing_intent=cast(
                        str,
                        data["processing_intent"],
                    ),
                    cleanup_policy=cast(
                        str,
                        data["cleanup_policy"],
                    ),
                    payload_schema_version=cast(
                        str,
                        data["payload_schema_version"],
                    ),
                    resource_rows=cast(
                        dict[str, JsonValue],
                        data["resource_rows"],
                    ),
                    processing_snapshot=cast(
                        dict[str, JsonValue],
                        data["processing_snapshot"],
                    ),
                    provenance=provenance,
                    created_by=getattr(request, "user", None),
                )
                if created:
                    phase = "metadata"
                    transfer_job = apply_transfer_metadata(transfer_job)
        except DjangoValidationError as exc:
            error("Transfer model validation failed")
            details = _django_validation_details(exc)
            event = (
                "hub.transfer_create_model_validation_failed"
                if phase == "registration"
                else "hub.transfer_metadata_apply_validation_failed"
            )
            _log_transfer_validation_failure(
                request,
                event=event,
                errors=cast(_ValidationErrorValue, details),
                transfer_key=cast(str, data["transfer_key"]),
            )
            return Response(
                {
                    "error": "Transfer payload validation failed",
                    "details": details,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            if phase == "registration":
                error("Transfer registration conflict")
                return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
            details = {"non_field_errors": [str(exc)]}
            error("Receiver metadata application failed")
            _log_transfer_validation_failure(
                request,
                event="hub.transfer_metadata_apply_failed",
                errors=cast(_ValidationErrorValue, details),
                transfer_key=cast(str, data["transfer_key"]),
            )
            return Response(
                {
                    "error": "Receiver could not apply transferred metadata",
                    "details": details,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_serializer = TransferJobStatusSerializer(transfer_job)
        response_payload = cast(
            dict[str, _TransferPayloadValue],
            _serialize_response_data(response_serializer),
        )
        decision("RECEIVER CREATE-TRANSFER RESULT")
        kv("Transfer created", created)
        kv("Transfer status", transfer_job.transfer_status)
        kv("Processing decision", transfer_job.processing_decision)
        success("Create-transfer request completed")
        return Response(
            response_payload,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferStatusView(APIView):
    authentication_classes: Sequence[type[BaseAuthentication]] = ()
    permission_classes = [AllowAny]

    def get(self, request: Request, transfer_key: str) -> Response:
        section("RECEIVER: TRANSFER STATUS", "🔎")
        kv("Transfer key", transfer_key)
        _assert_transfer_api_enabled()
        transfer_job = (
            TransferJob.objects.select_related(
                "source_center",
                "source_node",
                "target_node",
            )
            .filter(transfer_key=transfer_key)
            .first()
        )
        if transfer_job is None:
            raise Http404("Transfer job not found")

        transfer_source_node = cast(NetworkNode, getattr(transfer_job, "source_node"))
        authenticated_node = _enforce_transfer_node_auth(
            request, cast(str, getattr(transfer_source_node, "node_key"))
        )
        source_center = cast(
            Center | None, getattr(transfer_job, "source_center", None)
        )
        source_center_id = cast(int | None, getattr(source_center, "id", None))
        _assert_transfer_center_scope(authenticated_node, source_center_id)

        serializer = TransferJobStatusSerializer(transfer_job)
        payload = _serialize_response_data(serializer)
        decision("RECEIVER TRANSFER-STATUS RESULT")
        kv("Transfer status", transfer_job.transfer_status)
        kv("Processing decision", transfer_job.processing_decision)
        success("Transfer status returned")
        return Response(payload)


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferMediaUploadView(APIView):
    authentication_classes: Sequence[type[BaseAuthentication]] = ()
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(
        self,
        request: Request,
        transfer_key: str,
    ) -> Response:
        section("RECEIVER: MEDIA UPLOAD", "💾")
        kv("Transfer key", transfer_key)
        _assert_transfer_api_enabled()
        transfer_job = (
            TransferJob.objects.select_related(
                "source_center",
                "source_node",
                "target_node",
            )
            .filter(transfer_key=transfer_key)
            .first()
        )
        if transfer_job is None:
            raise Http404("Transfer job not found")

        transfer_source_node = cast(NetworkNode, getattr(transfer_job, "source_node"))
        authenticated_node = _enforce_transfer_node_auth(
            request,
            cast(str, getattr(transfer_source_node, "node_key")),
        )
        source_center = cast(
            Center | None, getattr(transfer_job, "source_center", None)
        )
        source_center_id = cast(int | None, getattr(source_center, "id", None))
        _assert_transfer_center_scope(authenticated_node, source_center_id)

        uploaded_file = cast(Mapping[str, UploadedFile], request.FILES).get("file")
        if not isinstance(uploaded_file, UploadedFile):
            error("Media upload did not include a multipart file")
            errors = {"file": "A multipart file upload is required"}
            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_validation_failed",
                errors=cast(_ValidationErrorValue, errors),
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )
            raise ValidationError(errors)

        request_data = cast(dict[str, object], request.data)
        media_role = str(request_data.get("media_role", "") or "").strip().lower()
        if media_role not in {"processed"}:
            error("Unsafe or unsupported media role was rejected")
            errors = {
                "media_role": (
                    "Only anonymized processed media may be uploaded for transfers."
                )
            }
            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_validation_failed",
                errors=cast(_ValidationErrorValue, errors),
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )
            raise ValidationError(errors)

        max_upload_bytes = int(
            getattr(settings, "ENDOREG_HUB_TRANSFER_MAX_UPLOAD_BYTES", 50 * 1024**3)
        )
        uploaded_size = uploaded_file.size
        kv("Uploaded size", uploaded_size)
        kv("Requested media role", media_role)
        if (
            max_upload_bytes <= 0
            or uploaded_size is None
            or uploaded_size < 0
            or uploaded_size > max_upload_bytes
        ):
            errors = {"file": "Uploaded media exceeds the configured size limit."}
            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_validation_failed",
                errors=cast(_ValidationErrorValue, errors),
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )
            raise ValidationError(errors)

        try:
            transfer_job = attach_transfer_media(
                transfer_job=transfer_job,
                uploaded_file=uploaded_file,
                media_role=media_role,
            )
        except DjangoValidationError as exc:
            error("Media attachment model validation failed")
            details = _django_validation_details(exc)
            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_model_validation_failed",
                errors=cast(_ValidationErrorValue, details),
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )
            raise ValidationError(
                {
                    "error": "Media attachment validation failed",
                    "details": details,
                }
            ) from exc
        except ValueError as exc:
            error("Media integrity or attachment validation failed")
            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_validation_failed",
                errors=cast(_ValidationErrorValue, {"detail": str(exc)}),
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )
            raise ValidationError({"detail": str(exc)}) from exc

        serializer = TransferJobStatusSerializer(transfer_job)
        payload = _serialize_response_data(serializer)
        decision("RECEIVER MEDIA-UPLOAD RESULT")
        kv("Transfer status", transfer_job.transfer_status)
        kv("Processing decision", transfer_job.processing_decision)
        success("Media-upload request completed")
        return Response(payload, status=status.HTTP_200_OK)
