from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ObjectDoesNotExist

from endoreg_db.models import AIDataSet
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.utils import set_default_center as sdc


class _FakeSettings:
    def __init__(self) -> None:
        self.center: Center | None = None
        self.processor: EndoscopyProcessor | None = None
        self.ai_dataset: AIDataSet | None = None
        self.ai_dataset_id: int | None = None
        self.annotator_name = "default-annotator"
        self.report_template_name = "default-template"
        self.ai_dataset_name = ""
        self.ai_dataset_type = ""
        self.save_kwargs: list[str] | None = None

    def save(self, *, update_fields: list[str] | None = None) -> None:
        self.save_kwargs = update_fields


@pytest.mark.django_db
def test_get_application_defaults_captures_snapshot_values() -> None:
    center = Center.objects.create(name="berlin", display_name="Berlin", center_key="B")
    processor = EndoscopyProcessor.objects.create(name="proc-1")

    fake = _FakeSettings()
    fake.center = center
    fake.processor = processor
    fake.annotator_name = "Alice"
    fake.report_template_name = "Default"
    fake.ai_dataset_name = "Dataset X"
    fake.ai_dataset_type = "image_multilabel"

    def get_settings() -> Any:
        return fake

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sdc, "get_application_settings", get_settings)
        snapshot = sdc.get_application_defaults()

    assert snapshot.center_id == center.pk
    assert snapshot.processor_id == processor.pk
    assert snapshot.annotator_name == "Alice"
    assert snapshot.report_template_name == "Default"
    assert snapshot.ai_dataset_name == "Dataset X"
    assert snapshot.ai_dataset_type == "image_multilabel"


def test_set_default_center_resolves_center_by_name_and_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center(name="main", display_name="Main Center", center_key="M-1")
    center.save()
    fake = _FakeSettings()
    monkeypatch.setattr(sdc, "get_application_settings", lambda: fake)

    sdc.set_default_center(center.pk)
    assert fake.center == center

    found = sdc.set_default_center("main")
    assert found.center == center
    assert fake.save_kwargs == ["center", "updated_at"]


@pytest.mark.django_db
def test_update_application_defaults_ignores_ai_dataset_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(name="dallas", display_name="Dallas")
    processor = EndoscopyProcessor.objects.create(name="proc-2")
    fake = _FakeSettings()
    fake.ai_dataset = None
    monkeypatch.setattr(sdc, "get_application_settings", lambda: fake)

    sdc.update_application_defaults(
        center=center.pk,
        processor=processor.name,
        annotator_name="Jane",
        report_template_name="T",
        ai_dataset_name="Name",
        ai_dataset_type="segment",
    )
    assert fake.center == center
    assert fake.processor == processor
    assert fake.annotator_name == "Jane"
    assert fake.report_template_name == "T"
    assert fake.ai_dataset_name == "Name"
    assert fake.ai_dataset is None
    assert fake.save_kwargs is None


@pytest.mark.django_db
def test_require_default_center_raises_when_not_set() -> None:
    fake = _FakeSettings()
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sdc, "get_application_settings", lambda: fake)
        with pytest.raises(ObjectDoesNotExist):
            sdc.require_default_center()

    fake.center = Center.objects.create(name="london", display_name="London")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sdc, "get_application_settings", lambda: fake)
        assert sdc.require_default_center() == fake.center
