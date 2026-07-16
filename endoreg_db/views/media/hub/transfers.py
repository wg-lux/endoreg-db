from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

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
    resolve_allowed_center_id,
    transfer_api_enabled,
)
from endoreg_db.services.hub.transfer_logging import (
    decision,
    error,
    info,
    json_block,
    kv,
    section,
    step,
    success,
    warning,
)
from endoreg_db.utils.observability.structured_logging import (
    emit_structured_event,
    hash_identifier,
)


logger = logging.getLogger(__name__)


def _assert_transfer_api_enabled() -> None:
    """
    Reject all hub-transfer requests unless this deployment is configured as
    an enabled central hub.
    """
    if transfer_api_enabled():
        return

    error("Hub transfer API is not enabled on this deployment")
    raise Http404("Hub transfer API is not enabled")


def _node_header(request, header_name: str) -> str:
    """
    Read and normalize a transfer-authentication header.

    The caller must never log the secret header value.
    """
    return str(request.headers.get(header_name, "") or "").strip()


def _safe_request_context(request) -> dict[str, Any]:
    """
    Build a privacy-safe request context for structured logs.
    """
    remote_addr = str(request.META.get("REMOTE_ADDR", "") or "").strip()

    return {
        "request_method": str(getattr(request, "method", "") or ""),
        "remote_addr_sha256": (
            hash_identifier(remote_addr)
            if remote_addr
            else None
        ),
    }


def _validation_error_fields(errors, prefix: str = "") -> list[str]:
    """
    Flatten nested DRF/Django validation errors into field paths for
    structured operational logging.
    """
    if isinstance(errors, dict):
        fields: list[str] = []

        for key, value in errors.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.extend(_validation_error_fields(value, path))

        return fields

    if isinstance(errors, list):
        fields: list[str] = []

        for item in errors:
            if isinstance(item, (dict, list)):
                fields.extend(_validation_error_fields(item, prefix))

        return fields or [prefix or "non_field_errors"]

    return [prefix or "non_field_errors"]


def _log_transfer_validation_failure(
    request,
    *,
    event: str,
    errors,
    transfer_key: str | None = None,
    transfer_job: TransferJob | None = None,
) -> None:
    """
    Emit privacy-safe structured validation diagnostics.

    Full payload details are printed only through transfer_logging, whose
    sanitizer must redact sensitive fields.
    """
    payload: dict[str, Any] = {
        **_safe_request_context(request),
        "error_fields": sorted(
            set(_validation_error_fields(errors))
        ),
    }

    if transfer_key:
        payload["transfer_key_sha256"] = hash_identifier(transfer_key)

    if transfer_job is not None:
        payload["transfer_job_id"] = str(transfer_job.pk)
        payload["resource_kind"] = transfer_job.resource_kind

    emit_structured_event(
        logger,
        event,
        level=logging.WARNING,
        **payload,
    )


def _django_validation_details(
    exc: DjangoValidationError,
) -> dict[str, Any]:
    """
    Convert a Django model ValidationError into a JSON-compatible response.
    """
    if hasattr(exc, "message_dict"):
        return exc.message_dict

    messages = getattr(exc, "messages", None)

    if messages:
        return {
            "non_field_errors": list(messages),
        }

    return {
        "non_field_errors": [str(exc)],
    }


def _assert_secure_transfer_transport(request) -> None:
    """
    Require HTTPS or an explicitly accepted equivalent transport.

    During the temporary SSH-tunnel development test, this check may be
    disabled through the process-local environment setting.
    """
    secure_transport_required = bool(
        getattr(
            settings,
            "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT",
            True,
        )
    )

    kv(
        "Secure transport required",
        secure_transport_required,
    )
    kv(
        "Request reports secure transport",
        request.is_secure(),
    )

    if not secure_transport_required:
        warning(
            "Secure-transport enforcement is disabled for this receiver process"
        )
        return

    if request.is_secure():
        success("Secure-transport requirement passed")
        return

    emit_structured_event(
        logger,
        "hub.transfer_secure_transport_failed",
        level=logging.WARNING,
        reason="insecure_request",
        **_safe_request_context(request),
    )

    error("Transfer rejected because the request is not considered secure")

    raise PermissionDenied(
        "Hub transfer requires HTTPS or equivalent secure transport"
    )


def _assert_transfer_mtls(request) -> None:
    """
    Require proxy-verified mutual TLS when configured.
    """
    mtls_required = bool(
        getattr(
            settings,
            "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS",
            False,
        )
    )

    kv("Mutual TLS required", mtls_required)

    if not mtls_required:
        info("Mutual TLS verification is disabled for this receiver process")
        return

    meta_key = str(
        getattr(
            settings,
            "ENDOREG_HUB_TRANSFER_MTLS_META_KEY",
            "",
        )
        or ""
    ).strip()

    expected_value = str(
        getattr(
            settings,
            "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE",
            "",
        )
        or ""
    ).strip()

    actual_value = str(
        request.META.get(meta_key, "")
        or ""
    ).strip()

    kv("mTLS metadata key configured", bool(meta_key))
    kv("mTLS expected value configured", bool(expected_value))
    kv("mTLS verification value present", bool(actual_value))

    if (
        meta_key
        and expected_value
        and actual_value == expected_value
    ):
        success("Proxy-verified mutual TLS requirement passed")
        return

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


def _enforce_transfer_node_auth(
    request,
    source_node_key: str,
):
    """
    Enforce transport security and authenticate the sending NetworkNode.
    """
    step("AUTH-1", "Validate transport security")

    _assert_secure_transfer_transport(request)
    _assert_transfer_mtls(request)

    step("AUTH-2", "Authenticate source network node")

    provided_node_key = _node_header(
        request,
        "X-Network-Node-Key",
    )
    provided_secret = _node_header(
        request,
        "X-Network-Node-Secret",
    )

    kv("Payload source node key", source_node_key)
    kv(
        "Authentication header node key",
        provided_node_key or "<missing>",
    )
    kv(
        "Authentication secret present",
        bool(provided_secret),
    )

    info(
        "The X-Network-Node-Secret value is intentionally never printed"
    )

    authenticated_node = authenticate_network_node(
        source_node_key=source_node_key,
        provided_node_key=provided_node_key,
        provided_secret=provided_secret,
    )

    if authenticated_node is None:
        error("Network-node authentication failed")

        raise PermissionDenied(
            "Invalid network node credentials for this transfer"
        )

    kv("Authenticated node ID", authenticated_node.pk)
    kv("Authenticated node key", authenticated_node.node_key)
    kv("Authenticated node role", authenticated_node.role)

    success("Network-node authentication passed")

    return authenticated_node


def _assert_transfer_center_scope(
    request,
    source_center_id: int | None,
) -> None:
    """
    Enforce authenticated user center scope when a scoped user is present.
    """
    allowed_center_id = resolve_allowed_center_id(
        getattr(request, "user", None)
    )

    kv("Transfer source center ID", source_center_id)
    kv("Authenticated allowed center ID", allowed_center_id)

    if allowed_center_id == -1:
        error("Authenticated user has no transfer-job access")

        raise PermissionDenied(
            "You do not have access to transfer jobs."
        )

    if (
        allowed_center_id is not None
        and allowed_center_id >= 0
        and source_center_id is not None
        and source_center_id != allowed_center_id
    ):
        error("Transfer source center is outside the authenticated scope")

        raise Http404("Transfer job not found")

    success("Transfer center scope check passed")


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferCreateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        section("RECEIVER: CREATE TRANSFER", "📥")

        kv("Request method", request.method)
        kv("Request path", request.path)
        kv(
            "Remote address present",
            bool(request.META.get("REMOTE_ADDR")),
        )
        kv("Request is secure", request.is_secure())
        kv(
            "Source node header",
            request.headers.get(
                "X-Network-Node-Key",
                "<missing>",
            ),
        )
        kv(
            "Node secret header present",
            bool(
                request.headers.get(
                    "X-Network-Node-Secret",
                    "",
                )
            ),
        )

        info("The node secret header value is never printed")

        json_block(
            "Incoming JSON payload",
            request.data,
        )

        step(1, "Verify hub transfer API availability")

        _assert_transfer_api_enabled()

        success("Hub transfer API is enabled")

        step(2, "Run DRF serializer validation")

        serializer = TransferJobCreateSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            error("Transfer request failed serializer validation")

            json_block(
                "Serializer errors",
                serializer.errors,
            )

            _log_transfer_validation_failure(
                request,
                event="hub.transfer_create_validation_failed",
                errors=serializer.errors,
            )

            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        success("DRF serializer validation passed")

        data = serializer.validated_data

        step(3, "Resolve source and target identities")

        source_node = data["source_node"]
        target_node = data["target_node"]
        source_center = data.get("source_center")

        kv("Source node ID", source_node.pk)
        kv("Source node key", source_node.node_key)
        kv("Source node role", source_node.role)
        kv("Target node ID", target_node.pk)
        kv("Target node key", target_node.node_key)
        kv("Target node role", target_node.role)
        kv(
            "Source center ID",
            getattr(source_center, "pk", None),
        )
        kv(
            "Source center key",
            getattr(source_center, "center_key", None),
        )

        success("Source and target identities resolved")

        step(4, "Authenticate source node")

        authenticated_node = _enforce_transfer_node_auth(
            request,
            source_node.node_key,
        )

        kv(
            "Authenticated node matches serializer node",
            authenticated_node.pk == source_node.pk,
        )

        step(5, "Validate center access scope")

        _assert_transfer_center_scope(
            request,
            getattr(source_center, "id", None),
        )

        step(6, "Create or reuse receiver TransferJob")

        kv("Transfer key", data["transfer_key"])
        kv("Resource kind", data["resource_kind"])
        kv("Resource hash", data["resource_hash"])
        kv("Transfer mode", data["transfer_mode"])
        kv("Processing policy", data["processing_policy"])
        kv("Processing intent", data["processing_intent"])
        kv("Cleanup policy", data["cleanup_policy"])
        kv(
            "Payload schema version",
            data["payload_schema_version"],
        )

        json_block(
            "Validated portable resource rows",
            data["resource_rows"],
        )
        json_block(
            "Validated processing snapshot",
            data["processing_snapshot"],
        )
        json_block(
            "Validated provenance",
            data.get("provenance") or {},
        )

        try:
            transfer_job, created = create_or_reuse_transfer_job(
                transfer_key=data["transfer_key"],
                source_node=source_node,
                target_node=target_node,
                source_center=source_center,
                resource_kind=data["resource_kind"],
                resource_hash=data["resource_hash"],
                transfer_mode=data["transfer_mode"],
                processing_policy=data["processing_policy"],
                processing_intent=data["processing_intent"],
                cleanup_policy=data["cleanup_policy"],
                payload_schema_version=data["payload_schema_version"],
                resource_rows=data["resource_rows"],
                processing_snapshot=data["processing_snapshot"],
                provenance=data.get("provenance") or {},
                created_by=getattr(request, "user", None),
            )

        except DjangoValidationError as exc:
            details = _django_validation_details(exc)

            error(
                "TransferJob model validation failed while persisting the transfer"
            )
            json_block(
                "TransferJob model validation details",
                details,
            )

            _log_transfer_validation_failure(
                request,
                event="hub.transfer_create_model_validation_failed",
                errors=details,
                transfer_key=data.get("transfer_key"),
            )

            return Response(
                {
                    "error": "Transfer payload validation failed",
                    "details": details,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except ValueError as exc:
            error("Transfer key conflict or transfer identity conflict")
            kv("Conflict reason", str(exc))

            return Response(
                {
                    "error": str(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )

        kv("TransferJob UUID", transfer_job.id)
        kv("TransferJob created", created)
        kv(
            "Initial transfer status",
            transfer_job.transfer_status,
        )
        kv(
            "Initial processing decision",
            transfer_job.processing_decision,
        )

        success(
            "Receiver TransferJob created"
            if created
            else "Existing receiver TransferJob reused"
        )

        if created:
            step(7, "Apply portable resource metadata")

            try:
                transfer_job = apply_transfer_metadata(
                    transfer_job
                )

            except DjangoValidationError as exc:
                details = _django_validation_details(exc)

                error(
                    "Receiver failed to apply transferred metadata because "
                    "model validation failed"
                )
                json_block(
                    "Metadata application validation details",
                    details,
                )

                _log_transfer_validation_failure(
                    request,
                    event="hub.transfer_metadata_apply_validation_failed",
                    errors=details,
                    transfer_key=transfer_job.transfer_key,
                    transfer_job=transfer_job,
                )

                return Response(
                    {
                        "error": (
                            "Receiver could not apply transferred metadata"
                        ),
                        "details": details,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            except ValueError as exc:
                error("Receiver failed to apply transferred metadata")
                kv("Metadata application error", str(exc))

                _log_transfer_validation_failure(
                    request,
                    event="hub.transfer_metadata_apply_failed",
                    errors={"detail": str(exc)},
                    transfer_key=transfer_job.transfer_key,
                    transfer_job=transfer_job,
                )

                return Response(
                    {
                        "error": (
                            "Receiver could not apply transferred metadata"
                        ),
                        "details": {
                            "non_field_errors": [str(exc)],
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            kv(
                "Receiver target object ID",
                transfer_job.target_object_id,
            )
            kv(
                "Transfer status",
                transfer_job.transfer_status,
            )
            kv(
                "Processing decision",
                transfer_job.processing_decision,
            )
            kv(
                "Status detail",
                transfer_job.status_detail,
            )
            kv(
                "Case resolution status",
                transfer_job.case_resolution_status,
            )
            kv(
                "Linked patient ID",
                transfer_job.linked_patient_id,
            )
            kv(
                "Linked patient examination ID",
                transfer_job.linked_patient_examination_id,
            )

            success("Portable resource metadata applied")

        else:
            warning(
                "Metadata application was skipped because the existing "
                "TransferJob was reused"
            )

        step(8, "Serialize receiver transfer status")

        response_serializer = TransferJobStatusSerializer(
            transfer_job
        )

        json_block(
            "Receiver create-transfer response",
            response_serializer.data,
        )

        decision("RECEIVER CREATE-TRANSFER RESULT")

        kv("TransferJob UUID", transfer_job.id)
        kv("Transfer key", transfer_job.transfer_key)
        kv("Transfer created", created)
        kv(
            "Receiver target object ID",
            transfer_job.target_object_id,
        )
        kv(
            "Final transfer status",
            transfer_job.transfer_status,
        )
        kv(
            "Processing decision",
            transfer_job.processing_decision,
        )
        kv("Status detail", transfer_job.status_detail)

        success("Create-transfer request completed")

        return Response(
            response_serializer.data,
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferStatusView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(
        self,
        request,
        transfer_key: str,
        *args,
        **kwargs,
    ):
        section("RECEIVER: TRANSFER STATUS", "🔎")

        kv("Request method", request.method)
        kv("Request path", request.path)
        kv("Transfer key", transfer_key)
        kv("Request is secure", request.is_secure())
        kv(
            "Source node header",
            request.headers.get(
                "X-Network-Node-Key",
                "<missing>",
            ),
        )
        info("The node secret header value is never printed")

        step(1, "Verify hub transfer API availability")

        _assert_transfer_api_enabled()

        step(2, "Resolve TransferJob by transfer key")

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
            error("No TransferJob exists for the supplied transfer key")
            raise Http404("Transfer job not found")

        kv("TransferJob UUID", transfer_job.id)
        kv("Source node", transfer_job.source_node.node_key)
        kv("Target node", transfer_job.target_node.node_key)
        kv("Resource kind", transfer_job.resource_kind)
        kv("Resource hash", transfer_job.resource_hash)
        kv("Transfer status", transfer_job.transfer_status)
        kv(
            "Receiver target object ID",
            transfer_job.target_object_id,
        )

        success("TransferJob resolved")

        step(3, "Authenticate source node")

        _enforce_transfer_node_auth(
            request,
            transfer_job.source_node.node_key,
        )

        step(4, "Validate center access scope")

        _assert_transfer_center_scope(
            request,
            transfer_job.source_center_id,
        )

        step(5, "Serialize transfer status")

        serializer = TransferJobStatusSerializer(
            transfer_job
        )

        json_block(
            "Transfer status response",
            serializer.data,
        )

        success("Transfer status returned")

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferMediaUploadView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(
        self,
        request,
        transfer_key: str,
        *args,
        **kwargs,
    ):
        section("RECEIVER: MEDIA UPLOAD", "💾")

        kv("Request method", request.method)
        kv("Request path", request.path)
        kv("Transfer key", transfer_key)
        kv("Request is secure", request.is_secure())
        kv(
            "Source node header",
            request.headers.get(
                "X-Network-Node-Key",
                "<missing>",
            ),
        )
        info("The node secret header value is never printed")

        step(1, "Verify hub transfer API availability")

        _assert_transfer_api_enabled()

        step(2, "Resolve TransferJob")

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
            error("TransferJob was not found for media upload")
            raise Http404("Transfer job not found")

        kv("TransferJob UUID", transfer_job.id)
        kv("Resource kind", transfer_job.resource_kind)
        kv("Resource hash", transfer_job.resource_hash)
        kv(
            "Current target object ID",
            transfer_job.target_object_id,
        )
        kv(
            "Current transfer status",
            transfer_job.transfer_status,
        )

        success("TransferJob resolved for media upload")

        step(3, "Authenticate source node")

        _enforce_transfer_node_auth(
            request,
            transfer_job.source_node.node_key,
        )

        step(4, "Validate center access scope")

        _assert_transfer_center_scope(
            request,
            transfer_job.source_center_id,
        )

        step(5, "Validate multipart media request")

        uploaded_file = request.FILES.get("file")

        if uploaded_file is None:
            errors = {
                "file": "A multipart file upload is required"
            }

            error("Media upload did not include a file")
            json_block("Media upload errors", errors)

            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_validation_failed",
                errors=errors,
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )

            return Response(
                errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        media_role = str(
            request.data.get("media_role", "")
            or ""
        ).strip().lower()

        kv(
            "Uploaded original filename",
            getattr(uploaded_file, "name", None),
        )
        kv(
            "Uploaded content type",
            getattr(uploaded_file, "content_type", None),
        )
        kv(
            "Uploaded size",
            getattr(uploaded_file, "size", None),
        )
        kv("Requested media role", media_role)

        if media_role not in {"processed"}:
            errors = {
                "media_role": (
                    "Only anonymized processed media may be "
                    "uploaded for transfers."
                )
            }

            error("Unsupported or unsafe media role")
            json_block("Media role errors", errors)

            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_validation_failed",
                errors=errors,
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )

            return Response(
                errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        success("Multipart media request validation passed")

        step(6, "Attach and verify transferred media")

        try:
            transfer_job = attach_transfer_media(
                transfer_job=transfer_job,
                uploaded_file=uploaded_file,
                media_role=media_role,
            )

        except DjangoValidationError as exc:
            details = _django_validation_details(exc)

            error(
                "Receiver model validation failed while attaching media"
            )
            json_block(
                "Media attachment validation details",
                details,
            )

            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_model_validation_failed",
                errors=details,
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )

            return Response(
                {
                    "error": "Media attachment validation failed",
                    "details": details,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except ValueError as exc:
            errors = {
                "detail": str(exc),
            }

            error("Media integrity or attachment validation failed")
            kv("Media attachment error", str(exc))

            _log_transfer_validation_failure(
                request,
                event="hub.transfer_media_upload_validation_failed",
                errors=errors,
                transfer_key=transfer_key,
                transfer_job=transfer_job,
            )

            return Response(
                errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        kv(
            "Receiver target object ID",
            transfer_job.target_object_id,
        )
        kv(
            "Transfer status",
            transfer_job.transfer_status,
        )
        kv(
            "Processing decision",
            transfer_job.processing_decision,
        )
        kv(
            "Status detail",
            transfer_job.status_detail,
        )

        json_block(
            "Updated transfer provenance",
            transfer_job.provenance,
        )

        success("Transferred media attached successfully")

        step(7, "Serialize media-upload response")

        serializer = TransferJobStatusSerializer(
            transfer_job
        )

        json_block(
            "Media upload response",
            serializer.data,
        )

        decision("RECEIVER MEDIA-UPLOAD RESULT")

        kv("TransferJob UUID", transfer_job.id)
        kv(
            "Receiver target object ID",
            transfer_job.target_object_id,
        )
        kv(
            "Final transfer status",
            transfer_job.transfer_status,
        )
        kv(
            "Processing decision",
            transfer_job.processing_decision,
        )
        kv("Status detail", transfer_job.status_detail)

        success("Media-upload request completed")

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )
