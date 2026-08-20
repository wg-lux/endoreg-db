from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

KNOWN_ENDOREG_API_URLS = {
    "/",
    "/endoreg-api/auth/bootstrap",
    "/endoreg-api/upload/",
    "/endoreg-api/upload/<uuid:id>/status/",
    "/endoreg-api/media/anonymization/metrics/",
    "/endoreg-api/media/videos/",
    "/endoreg-api/media/videos/<int:pk>/stream/",
    "/endoreg-api/media/pdfs/<int:pk>/stream/",
    "/endoreg-api/patient-examinations/list/",
}

KNOWN_ENDOREG_API_COMPATIBILITY_URLS = {
    "/api/auth/bootstrap",
    "/api/upload/",
    "/api/upload/<uuid:id>/status/",
    "/api/media/anonymization/metrics/",
    "/api/media/videos/",
    "/api/media/videos/<int:pk>/stream/",
    "/api/media/pdfs/<int:pk>/stream/",
    "/api/patient-examinations/list/",
}

KNOWN_BASE_API_URLS = {
    "/base_api/terminology/bundles",
    "/base_api/terminology/bundles/import",
    "/base_api/terminology/bundles/select",
    "/base_api/examinations/",
    "/base_api/examinations/<examination_id>/",
    "/base_api/examinations/<examination_id>/findings/",
    "/base_api/findings/<finding_id>/classifications/",
    "/base_api/classifications/<classification_id>/choices/",
    "/base_api/patient-examinations/<patient_examination_id>/dtypes-record/",
    "/base_api/patient-findings/",
    "/base_api/patient-findings/<patient_finding_id>/",
    "/base_api/patient-findings/<patient_finding_id>/classifications/",
    "/base_api/knowledge-bases/<module_name>/<version>/graph",
    "/base_api/knowledge-bases/<module_name>/<version>/examinations/<examination_name>/reporting-context",
}

KNOWN_DTYPES_API_URLS = {
    "/dtypes-api/terminology/bundles",
    "/dtypes-api/terminology/bundles/import",
    "/dtypes-api/terminology/bundles/select",
    "/dtypes-api/examinations/",
    "/dtypes-api/examinations/<examination_id>/",
    "/dtypes-api/examinations/<examination_id>/findings/",
    "/dtypes-api/findings/<finding_id>/classifications/",
    "/dtypes-api/classifications/<classification_id>/choices/",
    "/dtypes-api/patient-examinations/<patient_examination_id>/dtypes-record/",
    "/dtypes-api/patient-findings/",
    "/dtypes-api/patient-findings/<patient_finding_id>/",
    "/dtypes-api/patient-findings/<patient_finding_id>/classifications/",
    "/dtypes-api/knowledge-bases/<module_name>/<version>/graph",
    "/dtypes-api/knowledge-bases/<module_name>/<version>/examinations/<examination_name>/reporting-context",
}

LEGACY_FINDINGS_API_URLS = {
    "/api/examinations/<int:examination_id>/findings/",
    "/api/examinations/<pk>/findings/",
    "/api/findings/",
    "/api/findings/<int:finding_id>/classifications/",
    "/api/findings/<pk>/",
    "/api/classifications/",
    "/api/classifications/<int:classification_id>/choices/",
    "/api/classifications/<pk>/",
    "/api/patient-examinations/<int:exam_id>/classifications/",
    "/api/patient-examinations/<int:examination_id>/findings/",
    "/api/patient-findings/",
    "/api/patient-findings/<pk>/",
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
    missing_urls = sorted(
        (
            KNOWN_ENDOREG_API_URLS
            | KNOWN_ENDOREG_API_COMPATIBILITY_URLS
            | KNOWN_DTYPES_API_URLS
            | KNOWN_BASE_API_URLS
        )
        - url_patterns
    )

    assert not missing_urls, (
        "known API URLs are missing from `manage.py show_urls --format csv`:\n"
        + "\n".join(missing_urls)
    )


def test_lx_dtypes_base_api_is_not_mounted_under_endoreg_api(
    tmp_path: Path,
) -> None:
    urls_csv_path = tmp_path / "urls.csv"

    _export_show_urls_csv(urls_csv_path)

    url_patterns = _read_url_patterns(urls_csv_path)
    forbidden_urls = {
        prefixed
        for mount in ("/endoreg-api", "/api")
        for prefixed in (f"{mount}{url}" for url in KNOWN_BASE_API_URLS)
    }
    mounted_under_api = sorted(forbidden_urls & url_patterns)

    assert not mounted_under_api, (
        "lx-dtypes base_api routes must not be mounted under the endoreg API:\n"
        + "\n".join(mounted_under_api)
    )


def test_legacy_findings_api_routes_are_hard_cut(tmp_path: Path) -> None:
    urls_csv_path = tmp_path / "urls.csv"

    _export_show_urls_csv(urls_csv_path)

    url_patterns = _read_url_patterns(urls_csv_path)
    still_mounted = sorted(LEGACY_FINDINGS_API_URLS & url_patterns)

    assert not still_mounted, (
        "legacy endoreg findings routes must be cut in favor of /base_api/:\n"
        + "\n".join(still_mounted)
    )
