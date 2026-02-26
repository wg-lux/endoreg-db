from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from endoreg_db.models import PatientExamination, PatientExaminationReport


class ReportPdfRendererError(RuntimeError):
    pass


def get_renderer_binary() -> str | None:
    configured = os.environ.get("ENDOREG_REPORT_PDF_RENDERER_BIN", "").strip()
    if configured:
        return configured
    discovered = shutil.which("report_pdf_renderer")
    if discovered:
        return discovered
    return None


def build_report_template_pdf_payload(
    *,
    report: PatientExaminationReport,
    patient_examination: PatientExamination,
    frame_image_paths: list[str] | None = None,
    section_blocks: list[dict[str, Any]] | None = None,
    assets_root: str | None = None,
) -> dict[str, Any]:
    patient = patient_examination.patient
    header = {
        "center_name": getattr(getattr(patient, "center", None), "name", None),
        "patient_label": f"{getattr(patient, 'first_name', '')} {getattr(patient, 'last_name', '')}".strip()
        or None,
        "examination_date": str(getattr(patient_examination, "date_start", None) or "")
        or None,
        "report_version": str(getattr(report, "version", "")) or None,
    }

    blocks: list[dict[str, Any]] = []
    if section_blocks:
        blocks.extend(section_blocks)
    elif report.rendered_text:
        blocks.append({"type": "paragraph", "text": report.rendered_text})
    else:
        blocks.append(
            {
                "type": "sentence_group",
                "section_title": "Report",
                "variables": {},
                "sentences": [
                    {
                        "template": "{text}",
                        "enabled": True,
                        "variables": {"text": "No rendered report text available."},
                    }
                ],
            }
        )

    if frame_image_paths:
        blocks.append(
            {
                "type": "image_grid",
                "title": "Frames",
                "columns": 3,
                "image_paths": frame_image_paths,
                "captions": [f"frame {i + 1}" for i in range(len(frame_image_paths))],
            }
        )

    return {
        "title": report.title or f"{report.template_name} report",
        "subtitle": report.template_name,
        "header": header,
        "assets_root": assets_root,
        "blocks": blocks,
    }


def render_pdf_with_rust_renderer(
    payload: dict[str, Any],
    *,
    output_path: Path,
    timeout_seconds: int = 20,
) -> Path:
    binary = get_renderer_binary()
    if not binary:
        raise ReportPdfRendererError(
            "report_pdf_renderer binary not configured or found in PATH"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        tmp.flush()
        input_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            [binary, "--input", str(input_path), "--output", str(output_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if proc.returncode != 0:
            raise ReportPdfRendererError(
                f"renderer failed with exit code {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        if not output_path.exists():
            raise ReportPdfRendererError(
                "renderer completed without producing output pdf"
            )
        return output_path
    except subprocess.TimeoutExpired as exc:
        raise ReportPdfRendererError("renderer timed out") from exc
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except Exception:
            pass
