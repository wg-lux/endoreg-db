from __future__ import annotations

from typing import Any

from django.test import Client
import pytest

from lx_dtypes.django.api import main as api_main
from lx_dtypes.models.contracts.knowledge_base_graph import (
    KnowledgeBaseGraphSnapshot,
)


class _GraphKnowledgeBase:
    report_template: dict[str, object] = {"gastroscopy_report": object()}

    def export_core_concepts(self) -> dict[str, Any]:
        return {
            "module_name": "demo_graph",
            "knowledge_base_module": "demo_graph",
            "knowledge_base_version": "1.2.3",
            "examination": [
                {
                    "name": "gastroscopy",
                    "examination_types": [],
                    "findings": [],
                    "indications": [],
                }
            ],
        }

    def get_report_template_lifecycle_status(self, name: str) -> str:
        return "published"

    def export_report_template(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "version": "1.0.0",
            "examination": "gastroscopy",
            "lifecycle_status": "published",
        }


@pytest.mark.django_db(False)
def test_graph_routes_are_hosted_on_canonical_and_compatibility_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[tuple[str, str | None]] = []

    def load(module_name: str, *, version: str | None = None) -> _GraphKnowledgeBase:
        loaded.append((module_name, version))
        return _GraphKnowledgeBase()

    monkeypatch.setattr(api_main, "load_knowledge_base", load)
    client = Client()

    canonical = client.get(
        "/dtypes-api/knowledge-bases/demo_graph/1.2.3/graph", secure=True
    )
    compatibility = client.get(
        "/base_api/knowledge-bases/demo_graph/1.2.3/graph", secure=True
    )

    assert canonical.status_code == 200
    assert compatibility.status_code == 200
    assert loaded == [("demo_graph", "1.2.3"), ("demo_graph", "1.2.3")]

    snapshot = KnowledgeBaseGraphSnapshot.model_validate(canonical.json())
    assert snapshot.identity.canonical_name == "demo_graph@1.2.3"
    assert snapshot.contract_version == "knowledge_base_graph_v1"
    assert snapshot.report_templates[0].lifecycle_status == "published"


@pytest.mark.django_db(False)
def test_graph_route_rejects_unknown_examination_without_partial_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def load(*args: object, **kwargs: object) -> _GraphKnowledgeBase:
        return _GraphKnowledgeBase()

    monkeypatch.setattr(
        api_main,
        "load_knowledge_base",
        load,
    )

    response = Client().get(
        "/dtypes-api/knowledge-bases/demo_graph/1.2.3/examinations/unknown/"
        "reporting-context",
        secure=True,
    )

    assert response.status_code == 404
    assert "unknown" in response.json()["detail"]
