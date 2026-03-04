# api/viewsets/lookup.py
import logging
from ast import literal_eval
from collections.abc import Mapping

from django.core.cache import cache
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

# Use module import so tests can monkeypatch functions on the module
from endoreg_db.services import lookup_service as ls
from endoreg_db.schemas.lookup_state import (
    LookupInitRequest,
    LookupPartsPatchRequest,
    ValidationError,
    build_lookup_recompute_response,
    normalize_lookup_keys,
    validate_lookup_parts_response,
    validate_lookup_state,
)
from endoreg_db.services.lookup_store import DEFAULT_TTL_SECONDS, LookupStore
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.models.other.tag import Tag

ORIGIN_MAP_PREFIX = "lookup:origin:"
ISSUED_MAP_PREFIX = "lookup:issued_for_internal:"

logger = logging.getLogger(__name__)


class LookupViewSet(viewsets.ViewSet):
    """
    Django REST Framework ViewSet for managing lookup sessions.

    This ViewSet provides REST API endpoints for the lookup system, which
    evaluates medical examination requirements against patient data. It uses
    token-based sessions stored in Django cache to maintain state across
    multiple client requests.

    Key features:
    - Session initialization with patient examination data
    - Retrieval of lookup data by token
    - Partial updates to session data with automatic recomputation
    - Manual recomputation of derived data
    - Automatic session recovery for expired tokens

    The API supports both internal service tokens and public client tokens,
    with origin mapping to enable session restart functionality.

    Endpoints:
    - POST /lookup/init/: Initialize new lookup session
    - GET /lookup/{token}/all/: Retrieve complete session data
    - GET/PATCH /lookup/{token}/parts/: Get/update partial session data
    - POST /lookup/{token}/recompute/: Recompute by token
    - POST /lookup/recompute/: Token-less recompute via patient_examination_id
    """

    permission_classes = [EnvironmentAwarePermission]
    parser_classes = (JSONParser, FormParser, MultiPartParser)
    LOOKUP_LIFECYCLE_CONTRACT = "init -> all/parts -> recompute"
    INPUT_KEYS = {
        "patient_examination_id",
        "selected_requirement_set_ids",
        "selected_choices",
    }

    user_tags = Tag

    def _build_lookup_error(
        self,
        *,
        status_code: int,
        error_code: str,
        detail: str,
        next_step: str,
        lifecycle: str,
        token: str | None = None,
    ) -> Response:
        payload: dict[str, str | bool] = {
            "ok": False,
            "error_code": error_code,
            "detail": detail,
            "next_step": next_step,
            "lifecycle": lifecycle,
            "lifecycle_contract": self.LOOKUP_LIFECYCLE_CONTRACT,
        }
        if token:
            payload["token"] = token
        return Response(payload, status=status_code)

    def _build_init_payload(self, request) -> dict[str, object]:
        payload = normalize_lookup_keys(
            request.data if hasattr(request, "data") else {}
        )
        raw_pe = payload.get("patient_examination_id")

        # Fallback: parse malformed form payload where the entire dict was sent
        # as a single key string.
        if raw_pe is None:
            for candidate in (
                getattr(getattr(request, "_request", None), "POST", None),
                payload,
            ):
                try:
                    if isinstance(candidate, Mapping) and len(candidate.keys()) == 1:
                        only_key = next(iter(candidate.keys()))
                        if (
                            isinstance(only_key, str)
                            and only_key.startswith("{")
                            and only_key.endswith("}")
                        ):
                            try:
                                parsed = literal_eval(only_key)
                                if isinstance(parsed, dict):
                                    normalized = normalize_lookup_keys(parsed)
                                    if "patient_examination_id" in normalized:
                                        raw_pe = normalized.get(
                                            "patient_examination_id"
                                        )
                                        payload.update(normalized)
                                    logger.debug(
                                        "lookup.init recovered pe_id from malformed payload: %r",
                                        raw_pe,
                                    )
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

        if raw_pe is None:
            raw_pe = request.query_params.get("patient_examination_id")

        payload["patient_examination_id"] = raw_pe
        return payload

    def _issue_lookup_token(self, init_payload: LookupInitRequest) -> str:
        service_kwargs = {}
        if init_payload.user_tags:
            service_kwargs["user_tags"] = init_payload.user_tags

        internal_token = ls.create_lookup_token_for_pe(
            init_payload.patient_examination_id, **service_kwargs
        )
        internal_data = LookupStore(token=internal_token).get_all()

        issued_key = f"{ISSUED_MAP_PREFIX}{internal_token}"
        issued_count = cache.get(issued_key, 0)

        if issued_count == 0:
            token_to_return = internal_token
            cache.set(issued_key, 1, DEFAULT_TTL_SECONDS)
        else:
            public_store = LookupStore()
            token_to_return = public_store.init(
                initial=internal_data, ttl=DEFAULT_TTL_SECONDS
            )
            cache.set(issued_key, issued_count + 1, DEFAULT_TTL_SECONDS)

        cache.set(
            f"{ORIGIN_MAP_PREFIX}{token_to_return}",
            init_payload.patient_examination_id,
            DEFAULT_TTL_SECONDS,
        )
        return token_to_return

    @action(detail=False, methods=["post"])
    def init(self, request):
        """
        Initialize a new lookup session for a patient examination.

        Creates a new token-based session containing initial lookup data
        for the specified patient examination.

        Request body:
            patient_examination_id: Integer ID of the patient examination
            user_tags: Optional list of tag strings

        Returns:
            JSON response with session token

        Raises:
            400: Invalid patient_examination_id or creation failure
        """
        try:
            debug_data = getattr(request, "data", None)
            raw_post = getattr(getattr(request, "_request", None), "POST", None)
            body_preview = None
            try:
                body = getattr(getattr(request, "_request", None), "body", b"")
                body_preview = body[:200]
            except Exception:
                body_preview = None
            logger.debug(
                "lookup.init incoming: data=%r POST=%r body[:200]=%r",
                debug_data,
                raw_post,
                body_preview,
            )
        except Exception:
            pass

        payload = self._build_init_payload(request)
        raw_pe = payload.get("patient_examination_id")
        logger.debug("lookup.init raw_pe=%r type=%s", raw_pe, type(raw_pe))

        try:
            init_payload = LookupInitRequest.model_validate(payload)
        except ValidationError:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_init_invalid_patient_examination_id",
                detail="patient_examination_id must be a positive integer.",
                next_step=(
                    "Call POST /api/lookup/init/ with patient_examination_id in body "
                    "or query params."
                ),
                lifecycle="init",
            )

        try:
            token_to_return = self._issue_lookup_token(init_payload)
        except Exception as exc:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_init_failed",
                detail=str(exc),
                next_step=(
                    "Verify patient_examination_id exists, then retry "
                    "POST /api/lookup/init/."
                ),
                lifecycle="init",
            )

        return Response({"token": token_to_return}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="recompute")
    def recompute_without_token(self, request):
        """
        Recompute lookup data without an existing token.

        Contract: If the client has no token, it may send patient_examination_id
        and the backend will initialize a new lookup session server-side before
        recomputing.
        """
        payload = self._build_init_payload(request)

        try:
            init_payload = LookupInitRequest.model_validate(payload)
        except ValidationError:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_recompute_patient_examination_id_required",
                detail="patient_examination_id must be a positive integer for token-less recompute.",
                next_step=(
                    "Provide patient_examination_id to POST /api/lookup/recompute/ "
                    "or call POST /api/lookup/init/ first."
                ),
                lifecycle="recompute",
            )

        try:
            token = self._issue_lookup_token(init_payload)
            updates = ls.recompute_lookup(token)
            payload = build_lookup_recompute_response(token, updates)
            return Response(payload, status=status.HTTP_200_OK)
        except ValueError as exc:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_recompute_invalid_state",
                detail=str(exc),
                next_step=(
                    "Re-run POST /api/lookup/init/ and then POST "
                    "/api/lookup/{token}/recompute/ with the returned token."
                ),
                lifecycle="recompute",
            )
        except Exception as exc:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_recompute_failed",
                detail=str(exc),
                next_step=(
                    "Verify patient_examination_id and retry POST /api/lookup/recompute/."
                ),
                lifecycle="recompute",
            )

    @action(detail=True, methods=["get"], url_path="all")
    def get_all(self, request, pk=None):
        """
        Retrieve complete lookup data for a session token.

        Returns all stored data for the given token. If data is not found,
        attempts automatic session recovery using persisted origin mapping.

        Args:
            pk: Session token

        Returns:
            Complete lookup data dictionary

        Raises:
            404: Token not found and recovery failed
        """

        if not pk:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_token_required",
                detail="lookup token is required.",
                next_step=(
                    "Call POST /api/lookup/init/ with patient_examination_id, "
                    "then use GET /api/lookup/{token}/all/."
                ),
                lifecycle="all",
            )

        store = LookupStore(token=pk)

        try:
            validated_data = store.validate_and_recover_data(pk)
        except Exception:
            validated_data = None

        if validated_data is None:
            # Try automatic restart once using persisted origin mapping
            pe_id = cache.get(f"{ORIGIN_MAP_PREFIX}{pk}")

            if pe_id:
                try:
                    internal_token = ls.create_lookup_token_for_pe(int(pe_id))
                    new_data = LookupStore(token=internal_token).get_all()

                    if not new_data:
                        return self._build_lookup_error(
                            status_code=status.HTTP_404_NOT_FOUND,
                            error_code="lookup_data_unavailable_after_restart",
                            detail="Lookup data not available after restart.",
                            next_step=(
                                "Call POST /api/lookup/init/ and continue with the "
                                "returned token."
                            ),
                            lifecycle="all",
                            token=pk,
                        )

                    # Hydrate the original token with recovered data and refresh origin TTL
                    store.set_many(new_data)
                    cache.set(f"{ORIGIN_MAP_PREFIX}{pk}", pe_id, DEFAULT_TTL_SECONDS)
                    typed_data = validate_lookup_state(store.get_all())
                    return Response(typed_data, status=status.HTTP_200_OK)
                except Exception:
                    pass

            return self._build_lookup_error(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="lookup_session_not_found",
                detail="Lookup data not found or expired.",
                next_step=(
                    "Call POST /api/lookup/init/ with patient_examination_id and "
                    "retry with the new token."
                ),
                lifecycle="all",
                token=pk,
            )

        typed_data = validate_lookup_state(store.get_all())
        return Response(typed_data)

    @action(detail=True, methods=["get", "patch"], url_path="parts")
    def parts(self, request, pk=None):
        """
        Get or update partial lookup data for a session.

        GET: Retrieve specific keys from the session data.
        PATCH: Update session data and trigger recomputation if input keys changed.

        GET query params:
            keys: Comma-separated list of keys to retrieve

        PATCH body:
            updates: Dictionary of key-value pairs to update

        Args:
            pk: Session token

        Returns:
            GET: Dictionary with requested keys
            PATCH: Success confirmation

        Raises:
            404: Token not found
            400: Invalid request parameters
        """

        if not pk:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_token_required",
                detail="lookup token is required.",
                next_step=(
                    "Call POST /api/lookup/init/ with patient_examination_id, then "
                    "retry GET|PATCH /api/lookup/{token}/parts/."
                ),
                lifecycle="parts",
            )

        store = LookupStore(token=pk)

        if request.method == "GET":
            keys_param = request.query_params.get("keys", "")
            keys = [k.strip() for k in keys_param.split(",") if k.strip()]

            if not keys:
                return self._build_lookup_error(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    error_code="lookup_parts_keys_required",
                    detail="Provide ?keys=key1,key2.",
                    next_step=(
                        "Retry GET /api/lookup/{token}/parts/?keys="
                        "required_findings,requirement_status."
                    ),
                    lifecycle="parts",
                    token=pk,
                )

            if not store.get_all():
                return self._build_lookup_error(
                    status_code=status.HTTP_404_NOT_FOUND,
                    error_code="lookup_session_not_found",
                    detail="Lookup data not found or expired.",
                    next_step=(
                        "Call POST /api/lookup/init/ with patient_examination_id and "
                        "retry with the new token."
                    ),
                    lifecycle="parts",
                    token=pk,
                )

            try:
                payload = validate_lookup_parts_response(store.get_many(keys), keys)
                return Response(payload)
            except Exception:
                return self._build_lookup_error(
                    status_code=status.HTTP_404_NOT_FOUND,
                    error_code="lookup_session_not_found",
                    detail="Lookup data not found or expired.",
                    next_step=(
                        "Call POST /api/lookup/init/ with patient_examination_id and "
                        "retry with the new token."
                    ),
                    lifecycle="parts",
                    token=pk,
                )

        # PATCH
        try:
            patch_payload = LookupPartsPatchRequest.model_validate(request.data or {})
        except ValidationError:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_parts_invalid_updates",
                detail="updates must be a non-empty object.",
                next_step=(
                    "Retry PATCH /api/lookup/{token}/parts/ with {'updates': {...}}."
                ),
                lifecycle="parts",
                token=pk,
            )
        updates = patch_payload.updates

        if not store.get_all():
            return self._build_lookup_error(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="lookup_session_not_found",
                detail="Lookup data not found or expired.",
                next_step=(
                    "Call POST /api/lookup/init/ with patient_examination_id and "
                    "retry with the new token."
                ),
                lifecycle="parts",
                token=pk,
            )

        store.set_many(updates)

        if any(key in self.INPUT_KEYS for key in updates.keys()):
            try:
                ls.recompute_lookup(pk)
            except Exception as exc:
                logging.getLogger(__name__).error(
                    "Failed to recompute after patch for token %s: %s", pk, exc
                )

        return Response({"ok": True, "token": pk}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="recompute")
    def recompute(self, request, pk=None):
        """Recompute lookup data based on current PatientExamination and user selections."""

        if not pk:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_token_required",
                detail="lookup token is required.",
                next_step=(
                    "Call POST /api/lookup/recompute/ with patient_examination_id "
                    "or initialize with POST /api/lookup/init/."
                ),
                lifecycle="recompute",
            )

        try:
            updates = ls.recompute_lookup(pk)
            payload = build_lookup_recompute_response(pk, updates)
            return Response(payload, status=status.HTTP_200_OK)
        except ValueError as exc:
            detail = str(exc)
            if detail.startswith("No lookup data found for token"):
                return self._build_lookup_error(
                    status_code=status.HTTP_404_NOT_FOUND,
                    error_code="lookup_session_not_found",
                    detail=detail,
                    next_step=(
                        "Call POST /api/lookup/recompute/ with patient_examination_id "
                        "or initialize a new token via POST /api/lookup/init/."
                    ),
                    lifecycle="recompute",
                    token=pk,
                )
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_recompute_invalid_state",
                detail=detail,
                next_step=(
                    "Re-initialize session via POST /api/lookup/init/ and retry "
                    "POST /api/lookup/{token}/recompute/."
                ),
                lifecycle="recompute",
                token=pk,
            )
        except Exception as exc:
            return self._build_lookup_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="lookup_recompute_failed",
                detail=str(exc),
                next_step=(
                    "Retry recompute, or re-initialize using POST /api/lookup/init/."
                ),
                lifecycle="recompute",
                token=pk,
            )
