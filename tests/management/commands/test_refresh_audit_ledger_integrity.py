from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class RefreshAuditLedgerIntegrityCommandTests(TestCase):
    @patch(
        "endoreg_db.management.commands.refresh_audit_ledger_integrity.refresh_audit_ledger_integrity_status"
    )
    def test_default_refresh_prints_payload(self, refresh_mock) -> None:
        refresh_mock.return_value = {
            "status": "verified",
            "verified": True,
            "source": "refresh",
        }
        output = StringIO()

        call_command("refresh_audit_ledger_integrity", stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "verified")
        self.assertTrue(payload["verified"])
        refresh_mock.assert_called_once_with()

    @patch(
        "endoreg_db.management.commands.refresh_audit_ledger_integrity.refresh_audit_ledger_integrity_status_once"
    )
    def test_once_uses_lock_aware_refresh(self, refresh_once_mock) -> None:
        refresh_once_mock.return_value = {
            "status": "verified",
            "verified": True,
            "source": "skipped_locked",
        }
        output = StringIO()

        call_command("refresh_audit_ledger_integrity", "--once", stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["source"], "skipped_locked")
        refresh_once_mock.assert_called_once_with()

    @patch(
        "endoreg_db.management.commands.refresh_audit_ledger_integrity.refresh_audit_ledger_integrity_status"
    )
    def test_fail_on_non_verified_raises(self, refresh_mock) -> None:
        refresh_mock.return_value = {
            "status": "failed",
            "verified": False,
            "error": "tampered",
        }

        with self.assertRaises(CommandError):
            call_command("refresh_audit_ledger_integrity", "--fail-on-non-verified")
