from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase
from lx_dtypes.models.contracts.video_ai_labels import (
    VideoAiLabelPayload,
    VideoAiPredictionModelListPayload,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Label
from endoreg_db.views.video.ai.label import label_list, prediction_model_list


class _ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str


class VideoAnnotationLookupViewTest(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()

    def test_label_list_returns_sorted_typed_payload(self) -> None:
        polyp = Label.objects.create(name="polyp")
        outside = Label.objects.create(name="outside")
        request = self.factory.get("/api/media/videos/labels/list/")

        response = label_list(request)

        assert response.status_code == 200
        payload = TypeAdapter(list[VideoAiLabelPayload]).validate_json(response.content)
        assert [label.name for label in payload] == sorted(
            label.name for label in payload
        )
        payload_by_id = {label.id: label for label in payload}
        assert payload_by_id[int(outside.pk)] == VideoAiLabelPayload(
            id=int(outside.pk),
            name="outside",
        )
        assert payload_by_id[int(polyp.pk)] == VideoAiLabelPayload(
            id=int(polyp.pk),
            name="polyp",
        )

    @patch("endoreg_db.views.video.ai.label.Label.objects.all")
    def test_label_list_returns_typed_error_when_query_fails(
        self,
        all_labels: MagicMock,
    ) -> None:
        all_labels.side_effect = RuntimeError("database unavailable")
        request = self.factory.get("/api/media/videos/labels/list/")

        response = label_list(request)

        assert response.status_code == 500
        payload = _ErrorResponse.model_validate_json(response.content)
        assert payload.error == "Failed to fetch labels"

    @patch("endoreg_db.views.video.ai.label.ModelMeta.objects.select_related")
    def test_prediction_model_list_returns_typed_error_when_query_fails(
        self,
        select_related: MagicMock,
    ) -> None:
        select_related.side_effect = RuntimeError("database unavailable")
        request = self.factory.get("/api/media/videos/prediction-models/list/")

        response = prediction_model_list(request)

        assert response.status_code == 500
        payload = _ErrorResponse.model_validate_json(response.content)
        assert payload.error == "Failed to fetch video prediction models"

    @patch("endoreg_db.views.video.ai.label.ModelMeta.objects.select_related")
    def test_prediction_model_list_empty_state_matches_typed_contract(
        self,
        select_related: MagicMock,
    ) -> None:
        model_metas = MagicMock()
        select_related.return_value = model_metas
        model_metas.all.return_value = model_metas
        model_metas.order_by.return_value = []
        request = self.factory.get("/api/media/videos/prediction-models/list/")

        response = prediction_model_list(request)

        assert response.status_code == 200
        payload = VideoAiPredictionModelListPayload.model_validate_json(
            response.content
        )
        assert payload.models == []
        assert payload.default_huggingface_model_id
        assert payload.default_model_name
        assert payload.default_labelset_name
        assert len(payload.huggingface_models) == 1
