# api/viewsets/lookup.py
import logging
from typing import List

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from endoreg_db.services.lookup_store import LookupStore
from endoreg_db.services.lookup_service import (
    create_lookup_token_for_pe, 
    load_patient_exam_for_eval, 
    build_initial_lookup, 
    recompute_lookup
)


class LookupViewSet(viewsets.ViewSet):
    """
    Endpoints:
      POST /api/lookup/init/           { "patient_examination_id": 123 }
      GET  /api/lookup/{token}/all/
      GET  /api/lookup/{token}/parts/?keys=availableFindings,classificationChoices
      PATCH /api/lookup/{token}/parts/ { "updates": { "selectedRequirementSetIds": [1,2] } }
      POST /api/lookup/{token}/recompute/  # Recompute derived data
    """
    
    INPUT_KEYS = {"patient_examination_id", "selectedRequirementSetIds", "selectedChoices"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__name__)

    @action(detail=False, methods=["post"])
    def init(self, request):
        """Initialize lookup token for a PatientExamination"""
        pe_id = request.data.get("patient_examination_id")
        if not pe_id:
            return Response(
                {"detail": "patient_examination_id required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            token = create_lookup_token_for_pe(int(pe_id))
            self.logger.info(f"Created lookup token {token} for PE {pe_id}")
            return Response({"token": token}, status=status.HTTP_201_CREATED)
        except Exception as e:
            self.logger.error(f"Failed to create lookup token for PE {pe_id}: {e}")
            return Response(
                {"detail": str(e)}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=["get"], url_path="all")
    def get_all(self, request, pk=None):
        """Get all lookup data for a token with optional recovery"""
        store = LookupStore(token=pk)
        
        # Validate and attempt to recover corrupted data
        try:
            validated_data = store.validate_and_recover_data(pk)
        except ValueError as e:
            self.logger.warning(f"Validation failed for token {pk}: {e}")
            return Response(
                {"error": "Lookup data not found or expired", "token": pk}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        if validated_data is None:
            # Optional one-shot restart: try to recover PE id, then re-init via service
            pe_id = store._recover_patient_examination_id(pk)
            if pe_id:
                try:
                    new_token = create_lookup_token_for_pe(int(pe_id))
                    # If the new token got seeded, return 200 with the new token info
                    new_store = LookupStore(token=new_token)
                    new_data = new_store.get_all()
                    if new_data:
                        self.logger.info(f"Successfully restarted lookup: {pk} -> {new_token}")
                        return Response({
                            "restarted": True, 
                            "token": new_token, 
                            "data": new_data
                        }, status=status.HTTP_200_OK)
                except Exception as e:
                    self.logger.exception(f"Lookup restart failed for token {pk} / pe_id {pe_id}: {e}")
            
            return Response(
                {"error": "Lookup data not found or expired", "token": pk}, 
                status=status.HTTP_404_NOT_FOUND
            )
            
        # GET should not mutate data, so skip recompute
        # Recompute is only triggered by PATCH or explicit POST /recompute/
        return Response(validated_data)

    @action(detail=True, methods=["get", "patch"], url_path="parts")
    def parts(self, request, pk=None):
        """Get or update specific parts of lookup data"""
        store = LookupStore(token=pk)

        if request.method == "GET":
            keys_param = request.query_params.get("keys", "")
            keys = [k.strip() for k in keys_param.split(",") if k.strip()]
            if not keys:
                return Response(
                    {"detail": "Provide ?keys=key1,key2"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                data = store.get_many(keys)
                return Response(data)
            except Exception as e:
                self.logger.error(f"Failed to get parts {keys} for token {pk}: {e}")
                return Response(
                    {"detail": f"Failed to retrieve parts: {str(e)}"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

        # PATCH method
        updates = request.data.get("updates", {})
        if not isinstance(updates, dict):
            return Response(
                {"detail": "updates must be an object"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            store.set_many(updates)
            
            # Trigger recompute if any input keys were updated
            if any(key in self.INPUT_KEYS for key in updates.keys()):
                try:
                    recompute_lookup(pk)
                    self.logger.debug(f"Recomputed lookup after patch for token {pk}")
                except Exception as e:
                    self.logger.error(f"Failed to recompute after patch for token {pk}: {e}")
                    # Don't fail the entire request - the updates were still applied
            
            return Response({"ok": True, "token": pk}, status=status.HTTP_200_OK)
            
        except Exception as e:
            self.logger.error(f"Failed to update parts for token {pk}: {e}")
            return Response(
                {"detail": f"Failed to update parts: {str(e)}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=["post"], url_path="recompute")
    def recompute(self, request, pk=None):
        """Recompute lookup data based on current PatientExamination and user selections"""
        try:
            updates = recompute_lookup(pk)
            self.logger.info(f"Successfully recomputed lookup for token {pk}")
            return Response({
                "ok": True, 
                "token": pk,
                "updates": updates
            }, status=status.HTTP_200_OK)
        except ValueError as e:
            self.logger.warning(f"Lookup token {pk} not found for recompute: {e}")
            return Response(
                {"detail": str(e)}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            self.logger.error(f"Recompute failed for token {pk}: {e}")
            return Response(
                {"detail": f"Recompute failed: {e}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

