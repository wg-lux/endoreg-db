from __future__ import annotations

from rest_framework.request import Request
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.services.audit_integrity import get_audit_ledger_integrity_status
from endoreg_db.utils.permissions import EnvironmentAwarePermission


class AuditLedgerIntegrityStatusView(APIView):
    """
    Cheap frontend status endpoint for the audit-ledger green tick.

    This endpoint deliberately returns only the cached result of the last
    background verification. It must not call AuditLedger.verify_chain().
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request: Request) -> Response:
        return Response(
            get_audit_ledger_integrity_status(),
            status=status.HTTP_200_OK,
        )
