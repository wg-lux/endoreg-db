from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_assisted_reporting_route_contracts import (
    ContractError,
    check_contract,
    load_contract,
    parse_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_assisted_reporting_route_contracts.py"
CONTRACT = REPO_ROOT / "quality" / "assisted_reporting_route_contracts.yml"


def test_versioned_contract_is_strict_and_typed() -> None:
    contract = load_contract(CONTRACT)

    assert contract.schema_version == "1.0"
    assert {route.id for route in contract.routes} == {
        "terminology_bundles",
        "examination_findings",
        "patient_findings",
        "knowledge_base_graph",
        "examination_reporting_context",
    }
    assert all(route.owner_repository == "lx_dtypes" for route in contract.routes)
    examination_findings = next(
        route for route in contract.routes if route.id == "examination_findings"
    )
    assert examination_findings.query_parameters == (
        "module_name",
        "module_version",
        "patient_examination_id",
    )


def test_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ContractError, match="keys differ"):
        parse_contract(
            {
                "schema_version": "1.0",
                "wire_contract": {},
                "routes": [],
                "forbidden_frontend_markers": [],
                "unexpected": True,
            }
        )


def test_contract_rejects_camel_case_wire_parameters() -> None:
    value = {
        "schema_version": "1.0",
        "wire_contract": {
            "backend_fields": "snake_case",
            "frontend_response_fields": "camelCase",
            "request_conversion_owner": "frontend/src/api/axiosInstance.ts",
            "response_conversion_owner": "frontend/src/api/axiosInstance.ts",
        },
        "routes": [
            {
                "id": "invalid",
                "method": "GET",
                "path": "/dtypes-api/examinations/{examinationId}/findings/",
                "owner_repository": "lx_dtypes",
                "implementation_file": "route.py",
                "implementation_marker": "marker",
                "frontend_file": "route.ts",
                "frontend_markers": ["marker"],
                "query_parameters": [],
            }
        ],
        "forbidden_frontend_markers": [],
    }

    with pytest.raises(ContractError, match="non-snake_case"):
        parse_contract(value)


def test_checker_detects_frontend_route_drift(tmp_path: Path) -> None:
    contract = load_contract(CONTRACT)
    endoreg_root = tmp_path / "endoreg"
    dtypes_root = tmp_path / "dtypes"
    annotate_root = tmp_path / "annotate"
    (endoreg_root / "endoreg_db").mkdir(parents=True)
    (endoreg_root / "endoreg_db" / "root_urls.py").write_text(
        "DTYPES_API_PREFIX\n_dtypes_api_urlconf()\n", encoding="utf-8"
    )
    conversion_path = annotate_root / contract.wire_contract.request_conversion_owner
    conversion_path.parent.mkdir(parents=True)
    conversion_path.write_text(
        "localSnakecaseKeys\ncamelcaseKeys(data, { deep: true })\n", encoding="utf-8"
    )
    for route in contract.routes:
        implementation = dtypes_root / route.implementation_file
        implementation.parent.mkdir(parents=True, exist_ok=True)
        with implementation.open("a", encoding="utf-8") as stream:
            stream.write(f"{route.implementation_marker}\n")
        frontend = annotate_root / route.frontend_file
        frontend.parent.mkdir(parents=True, exist_ok=True)
        with frontend.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(route.frontend_markers) + "\n")
    findings_source = annotate_root / "frontend" / "src" / "api" / "findingsApi.ts"
    with findings_source.open("a", encoding="utf-8") as stream:
        stream.write("VITE_FINDINGS_BACKEND\n")

    with pytest.raises(ContractError, match="forbidden frontend marker"):
        check_contract(
            contract,
            endoreg_root=endoreg_root,
            lx_dtypes_root=dtypes_root,
            lx_annotate_root=annotate_root,
        )


def test_repository_contract_cli() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "verified 5 canonical routes" in result.stdout
