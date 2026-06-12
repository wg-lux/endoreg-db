from __future__ import annotations

import json
from io import StringIO
from typing import Any, cast
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from lx_dtypes.models.contracts.audit_ledger import (
    AuditLedgerIntegrityStatusPayload,
)
from lx_dtypes.models.contracts.management_command import (
    RefreshAuditLedgerIntegrityCommandOptionsPayload,
)



def _status_payload(
    *,
    status: str,
    verified: bool,
    source: str,
    error: str | None = None,
) -> dict[str, object]:
    return AuditLedgerIntegrityStatusPayload(
        status=cast(Any, status),
        verified=verified,
        source=source,
        error=error,
        ledger_head_hash="0" * 64,
    ).model_dump(mode="python")


def _command_options(
    *,
    once: bool = False,
    pretty: bool = False,
    fail_on_non_verified: bool = False,
) -> dict[str, object]:
    return RefreshAuditLedgerIntegrityCommandOptionsPayload(
        once=once,
        pretty=pretty,
        fail_on_non_verified=fail_on_non_verified,
    ).model_dump(mode="python")


def _payload_from_output(output: StringIO) -> dict[str, object]:
    return cast(dict[str, object], json.loads(output.getvalue()))


class RefreshAuditLedgerIntegrityCommandTests(TestCase):
    @patch(
        "endoreg_db.management.commands.refresh_audit_ledger_integrity.refresh_audit_ledger_integrity_status"
    )
    def test_default_refresh_prints_payload(
        self,
        refresh_mock: MagicMock,
    ) -> None:
        refresh_mock.return_value = _status_payload(
            status="verified",
            verified=True,
            source="refresh",
        )
        output = StringIO()

        call_command(
            "refresh_audit_ledger_integrity",
            stdout=output,
            **_command_options(),
        )

        payload = _payload_from_output(output)
        self.assertEqual(payload["status"], "verified")
        self.assertTrue(payload["verified"])
        refresh_mock.assert_called_once_with()

    @patch(
        "endoreg_db.management.commands.refresh_audit_ledger_integrity.refresh_audit_ledger_integrity_status_once"
    )
    def test_once_uses_lock_aware_refresh(
        self,
        refresh_once_mock: MagicMock,
    ) -> None:
        refresh_once_mock.return_value = _status_payload(
            status="verified",
            verified=True,
            source="skipped_locked",
        )
        output = StringIO()

        call_command(
            "refresh_audit_ledger_integrity",
            stdout=output,
            **_command_options(once=True),
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["source"], "skipped_locked")
        refresh_once_mock.assert_called_once_with()

    @patch(
        "endoreg_db.management.commands.refresh_audit_ledger_integrity.refresh_audit_ledger_integrity_status"
    )
    def test_fail_on_non_verified_raises(
        self,
        refresh_mock: MagicMock,
    ) -> None:
        refresh_mock.return_value = _status_payload(
            status="failed",
            verified=False,
            source="refresh",
            error="tampered",
        )

        with self.assertRaises(CommandError):
            call_command(
                "refresh_audit_ledger_integrity",
                **_command_options(fail_on_non_verified=True),
            )
