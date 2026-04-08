from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from endoreg_db.services.environment_readiness import check_environment_readiness
from endoreg_db.utils.file_operations import atomic_copy_file
from endoreg_db.utils.paths import LOG_DIR, PROTECTED_DATA_ROOT, STORAGE_DIR

SECRET_KEY_FINGERPRINT_FILE = LOG_DIR / ".secret_key_fingerprint"


def _secret_key_fingerprint() -> str:
    secret_key_file = os.environ.get("DJANGO_SECRET_KEY_FILE", "").strip()
    if secret_key_file:
        content = Path(secret_key_file).read_text(encoding="utf-8").strip()
    else:
        content = str(settings.SECRET_KEY)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _path_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


class Command(BaseCommand):
    help = (
        "Validate the LuxNix runtime contract for protected media, group access, "
        "and stable SECRET_KEY derivation."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the health report as JSON.",
        )

    def handle(self, *args, **options) -> None:
        protected_media_url = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "").strip()
        protected_media_root = Path(
            os.environ.get("PROTECTED_MEDIA_ROOT", str(STORAGE_DIR))
        ).resolve()
        current_gid = os.getgid()
        supplemental_gids = set(os.getgroups())
        media_gid = (
            protected_media_root.stat().st_gid if protected_media_root.exists() else -1
        )
        secret_fingerprint = _secret_key_fingerprint()

        previous_fingerprint = ""
        if SECRET_KEY_FINGERPRINT_FILE.exists():
            previous_fingerprint = SECRET_KEY_FINGERPRINT_FILE.read_text(
                encoding="utf-8"
            ).strip()
        else:
            SECRET_KEY_FINGERPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp_file = SECRET_KEY_FINGERPRINT_FILE.with_suffix(".tmp")
            temp_file.write_text(secret_fingerprint, encoding="utf-8")
            atomic_copy_file(
                source=temp_file,
                destination=SECRET_KEY_FINGERPRINT_FILE,
                preserve_metadata=False,
                file_mode=0o640,
                dir_mode=0o750,
            )
            temp_file.unlink(missing_ok=True)

        checks = {
            "protected_media_url_reachable": protected_media_url == "/protected_media/",
            "protected_media_root_exists": protected_media_root.exists(),
            "protected_media_root_within_protected_data": _path_within(
                PROTECTED_DATA_ROOT, protected_media_root
            ),
            "app_user_has_media_gid": (
                protected_media_root.exists()
                and (current_gid == media_gid or media_gid in supplemental_gids)
            ),
            "secret_key_stable": (
                not previous_fingerprint or previous_fingerprint == secret_fingerprint
            ),
        }
        readiness_issues = check_environment_readiness()
        payload = {
            "checks": checks,
            "readiness_issues": [
                {
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                }
                for issue in readiness_issues
            ],
            "protected_media_root": str(protected_media_root),
            "protected_media_url": protected_media_url,
            "storage_root": str(STORAGE_DIR.resolve()),
            "secret_key_fingerprint": secret_fingerprint,
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for key, value in checks.items():
                marker = self.style.SUCCESS("ok") if value else self.style.ERROR("fail")
                self.stdout.write(f"{marker} {key}")
            for issue in readiness_issues:
                marker = (
                    self.style.ERROR("fail")
                    if issue.severity == "critical"
                    else self.style.WARNING("warn")
                )
                path_suffix = f" ({issue.path})" if issue.path else ""
                self.stdout.write(
                    f"{marker} {issue.code}: {issue.message}{path_suffix}"
                )

        failed = [key for key, value in checks.items() if not value]
        failed.extend(
            issue.code for issue in readiness_issues if issue.severity == "critical"
        )
        if failed:
            raise CommandError(f"System health check failed: {', '.join(failed)}")
