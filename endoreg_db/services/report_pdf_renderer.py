from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from endoreg_db.config.env import get_report_pdf_renderer_bin
from endoreg_db.utils.filesystem.file_operations import (
    atomic_move_file,
    atomic_write_file,
    ensure_directory,
    safe_unlink_file,
)

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport


class ReportPdfRendererError(RuntimeError):
    pass


def get_renderer_binary() -> str | None:
    configured = get_report_pdf_renderer_bin()
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
    frame_captions: list[str] | None = None,
    section_blocks: list[dict[str, Any]] | None = None,
    assets_root: str | None = None,
    patient_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    patient = patient_examination.patient
    identity = patient_identity or {}
    first_name = str(identity.get("first_name") or getattr(patient, "first_name", ""))
    last_name = str(identity.get("last_name") or getattr(patient, "last_name", ""))
    dob = identity.get("dob", getattr(patient, "dob", None))
    header = {
        "center_name": getattr(getattr(patient, "center", None), "name", None),
        "patient_label": f"{first_name} {last_name}".strip() or None,
        "patient_birth_date": str(dob or "") or None,
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
                "captions": frame_captions
                if frame_captions
                else [f"frame {i + 1}" for i in range(len(frame_image_paths))],
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

    ensure_directory(output_path.parent)
    unique_suffix = uuid.uuid4().hex
    input_path = output_path.with_name(f".{output_path.name}.{unique_suffix}.json")
    temp_output_path = output_path.with_name(f".{output_path.name}.{unique_suffix}.tmp")
    atomic_write_file(
        destination=input_path,
        content=[json.dumps(payload, ensure_ascii=False).encode("utf-8")],
    )

    try:
        proc = subprocess.run(
            [binary, "--input", str(input_path), "--output", str(temp_output_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if proc.returncode != 0:
            raise ReportPdfRendererError(
                f"renderer failed with exit code {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        if not temp_output_path.exists():
            raise ReportPdfRendererError(
                "renderer completed without producing output pdf"
            )
        atomic_move_file(source=temp_output_path, destination=output_path)
        return output_path
    except subprocess.TimeoutExpired as exc:
        raise ReportPdfRendererError("renderer timed out") from exc
    finally:
        try:
            safe_unlink_file(input_path, missing_ok=True)
            safe_unlink_file(temp_output_path, missing_ok=True)
        except Exception:
            pass
