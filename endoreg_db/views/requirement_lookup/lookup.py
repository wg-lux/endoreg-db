# api/viewsets/lookup.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from typing import List
from endoreg_db.services.lookup_store import LookupStore
from endoreg_db.services.lookup_service import create_lookup_token_for_pe, load_patient_exam_for_eval, build_initial_lookup

class LookupViewSet(viewsets.ViewSet):
    """
    Endpoints:
      POST /api/lookup/init/           { "patient_examination_id": 123 }
      GET  /api/lookup/{token}/all/
      GET  /api/lookup/{token}/parts/?keys=availableFindings,classificationChoices
      PATCH /api/lookup/{token}/parts/ { "updates": { "selectedRequirementSetIds": [1,2] } }
    """

    @action(detail=False, methods=["post"])
    def init(self, request):
        pe_id = request.data.get("patient_examination_id")
        if not pe_id:
            return Response({"detail": "patient_examination_id required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = create_lookup_token_for_pe(int(pe_id))
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"token": token}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="all")
    def get_all(self, request, pk=None):
        store = LookupStore(token=pk)
        return Response(store.get_all())

    @action(detail=True, methods=["get"], url_path="parts")
    def get_parts(self, request, pk=None):
        store = LookupStore(token=pk)
        keys_param = request.query_params.get("keys", "")
        keys: List[str] = [k.strip() for k in keys_param.split(",") if k.strip()]
        if not keys:
            return Response({"detail": "Provide ?keys=key1,key2"}, status=status.HTTP_400_BAD_REQUEST)
        return Response(store.get_many(keys))

    @action(detail=True, methods=["patch"], url_path="parts")
    def patch_parts(self, request, pk=None):
        store = LookupStore(token=pk)
        updates = request.data.get("updates", {})
        if not isinstance(updates, dict):
            return Response({"detail": "updates must be an object"}, status=status.HTTP_400_BAD_REQUEST)
        store.set_many(updates)
        return Response({"ok": True, "token": pk}, status=status.HTTP_200_OK)
