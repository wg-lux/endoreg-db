from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

KNOWN_API_URLS = {
    "/",
    "/api/auth/bootstrap",
    "/api/upload/",
    "/api/upload/<uuid:id>/status/",
    "/api/media/anonymization/metrics/",
    "/api/media/videos/",
    "/api/media/videos/<int:pk>/stream/",
    "/api/media/pdfs/<int:pk>/stream/",
    "/api/patient-examinations/list/",
}


def _export_show_urls_csv(output_path: Path) -> None:
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.test"

    with output_path.open("w", encoding="utf-8", newline="") as urls_csv:
        subprocess.run(
            [
                sys.executable,
                "manage.py",
                "show_urls",
                "--format",
                "csv",
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=urls_csv,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )


def _read_url_patterns(csv_path: Path) -> set[str]:
    with csv_path.open(encoding="utf-8", newline="") as urls_csv:
        rows = csv.reader(urls_csv)
        return {row[0] for row in rows if row}


def test_show_urls_csv_contains_known_api_urls(tmp_path: Path) -> None:
    urls_csv_path = tmp_path / "urls.csv"

    _export_show_urls_csv(urls_csv_path)

    url_patterns = _read_url_patterns(urls_csv_path)
    missing_urls = sorted(KNOWN_API_URLS - url_patterns)

    assert not missing_urls, (
        "known API URLs are missing from `manage.py show_urls --format csv`:\n"
        + "\n".join(missing_urls)
    )
