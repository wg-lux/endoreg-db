from __future__ import annotations

from django.conf import settings
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import TransferJob
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
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission


def _assert_transfer_api_enabled() -> None:
    if not bool(getattr(settings, "ENDOREG_ENABLE_HUB_TRANSFERS", False)):
        raise Http404("Hub transfer API is not enabled")


def _node_header(request, header_name: str) -> str:
    return str(request.headers.get(header_name, "") or "").strip()


def _assert_secure_transfer_transport(request) -> None:
    if not bool(
        getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT", True)
    ):
        return
    if request.is_secure():
        return
    raise PermissionDenied("Hub transfer requires HTTPS or equivalent secure transport")


def _assert_transfer_mtls(request) -> None:
    if not bool(getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS", False)):
        return
    meta_key = str(
        getattr(settings, "ENDOREG_HUB_TRANSFER_MTLS_META_KEY", "") or ""
    ).strip()
    expected_value = str(
        getattr(settings, "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE", "") or ""
    ).strip()
    actual_value = str(request.META.get(meta_key, "") or "").strip()
    if not meta_key or not expected_value or actual_value != expected_value:
        raise PermissionDenied(
            "Hub transfer requires proxy-verified mutual TLS client authentication"
        )


def _enforce_transfer_node_auth(request, source_node_key: str) -> None:
    _assert_secure_transfer_transport(request)
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return

    _assert_transfer_mtls(request)
    provided_node_key = _node_header(request, "X-Network-Node-Key")
    provided_secret = _node_header(request, "X-Network-Node-Secret")
    authenticated_node = authenticate_network_node(
        source_node_key=source_node_key,
        provided_node_key=provided_node_key,
        provided_secret=provided_secret,
    )
    if authenticated_node is None:
        raise PermissionDenied("Invalid network node credentials for this transfer")


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferCreateView(APIView):
    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, *args, **kwargs):
        _assert_transfer_api_enabled()
        serializer = TransferJobCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        _enforce_transfer_node_auth(request, data["source_node"].node_key)

        try:
            transfer_job, created = create_or_reuse_transfer_job(
                transfer_key=data["transfer_key"],
                source_node=data["source_node"],
                target_node=data["target_node"],
                source_center=data.get("source_center"),
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
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)

        if created:
            transfer_job = apply_transfer_metadata(transfer_job)

        response_serializer = TransferJobStatusSerializer(transfer_job)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferStatusView(APIView):
    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, transfer_key: str, *args, **kwargs):
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

        _enforce_transfer_node_auth(request, transfer_job.source_node.node_key)

        allowed_center_id = resolve_allowed_center_id(getattr(request, "user", None))
        if (
            allowed_center_id is not None
            and allowed_center_id != -1
            and transfer_job.source_center_id is not None
            and transfer_job.source_center_id != allowed_center_id
        ):
            raise Http404("Transfer job not found")

        serializer = TransferJobStatusSerializer(transfer_job)
        return Response(serializer.data)


@method_decorator(csrf_exempt, name="dispatch")
class HubTransferMediaUploadView(APIView):
    permission_classes = [EnvironmentAwarePermission]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, transfer_key: str, *args, **kwargs):
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

        _enforce_transfer_node_auth(request, transfer_job.source_node.node_key)

        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            raise ValidationError({"file": "A multipart file upload is required"})

        media_role = str(request.data.get("media_role", "") or "").strip().lower()
        if media_role not in {"processed"}:
            raise ValidationError(
                {
                    "media_role": (
                        "Only anonymized processed media may be uploaded for transfers."
                    )
                }
            )

        try:
            transfer_job = attach_transfer_media(
                transfer_job=transfer_job,
                uploaded_file=uploaded_file,
                media_role=media_role,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        serializer = TransferJobStatusSerializer(transfer_job)
        return Response(serializer.data, status=status.HTTP_200_OK)
