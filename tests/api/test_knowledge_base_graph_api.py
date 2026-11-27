from __future__ import annotations

from pathlib import Path

from django.test import Client
from lx_dtypes.models.contracts.knowledge_base_graph import KnowledgeBaseGraphSnapshot


def test_graph_routes_use_canonical_mount(packaged_registry: Path) -> None:
    client = Client()
    response = client.get(
        "/dtypes-api/knowledge-bases/dgvs_reporting/0.1.0/graph", secure=True
    )
    assert response.status_code == 200, response.content.decode()
    snapshot = KnowledgeBaseGraphSnapshot.model_validate(response.json())
    assert snapshot.identity.canonical_name == "dgvs_reporting@0.1.0"
    assert snapshot.contract_version == "knowledge_base_graph_v1"
    assert snapshot.report_templates
    assert (
        client.get(
            "/base_api/knowledge-bases/dgvs_reporting/0.1.0/graph", secure=True
        ).status_code
        == 404
    )


def test_graph_route_rejects_unknown_examination_without_partial_context(
    packaged_registry: Path,
) -> None:
    response = Client().get(
        "/dtypes-api/knowledge-bases/dgvs_reporting/0.1.0/examinations/unknown/"
        "reporting-context",
        secure=True,
    )
    assert response.status_code == 404
    assert "unknown" in response.json()["detail"]
