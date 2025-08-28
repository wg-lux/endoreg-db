# api/viewsets/lookup.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from typing import List
from endoreg_db.services.lookup_store import LookupStore
from endoreg_db.services.lookup_service import create_lookup_token_for_pe, load_patient_exam_for_eval, build_initial_lookup, recompute_lookup

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

    @action(detail=False, methods=["post"])
    def init(self, request):
        pe_id = request.data.get("patient_examination_id")
        if not pe_id:
            return Response({"detail": "patient_examination_id required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = create_lookup_token_for_pe(int(pe_id))
            print(f"Created lookup token {token} for PE {pe_id}")
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"token": token}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="all")
    def get_all(self, request, pk=None):
        store = LookupStore(token=pk)
        
        # Validate and attempt to recover corrupted data
        validated_data = store.validate_and_recover_data(pk)
        if validated_data is None:
            return Response({
                "error": "Lookup data not found or expired",
                "token": pk
            }, status=status.HTTP_404_NOT_FOUND)
            
        # GET should not mutate data, so skip recompute
        # Recompute is only triggered by PATCH or explicit POST /recompute/
        
        return Response(store.get_all())

    @action(detail=True, methods=["get", "patch"], url_path="parts")
    def parts(self, request, pk=None):
        store = LookupStore(token=pk)

        if request.method == "GET":
            keys_param = request.query_params.get("keys", "")
            keys = [k.strip() for k in keys_param.split(",") if k.strip()]
            if not keys:
                return Response({"detail": "Provide ?keys=key1,key2"}, status=status.HTTP_400_BAD_REQUEST)
            return Response(store.get_many(keys))

        # PATCH
        updates = request.data.get("updates", {})
        if not isinstance(updates, dict):
            return Response({"detail": "updates must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        store.set_many(updates)

        if any(key in self.INPUT_KEYS for key in updates.keys()):
            try:
                recompute_lookup(pk)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    "Failed to recompute after patch for token %s: %s", pk, e
                )
        return Response({"ok": True, "token": pk}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="recompute")
    def recompute(self, request, pk=None):
        """Recompute lookup data based on current PatientExamination and user selections"""
        try:
            updates = recompute_lookup(pk)
            return Response({
                "ok": True, 
                "token": pk,
                "updates": updates
            }, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"detail": f"Recompute failed: {e}"}, status=status.HTTP_400_BAD_REQUEST)

