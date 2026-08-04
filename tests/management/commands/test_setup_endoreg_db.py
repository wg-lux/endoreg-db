from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable, cast

import pytest
from django.test import override_settings
from pydantic import ValidationError
from pytest import MonkeyPatch

from endoreg_db.management.commands import setup_endoreg_db as setup_command


def _options(
    *,
    skip_ai_setup: bool = False,
    force_recreate: bool = False,
    yaml_only: bool = False,
) -> dict[str, object]:
    return {
        "skip_ai_setup": skip_ai_setup,
        "force_recreate": force_recreate,
        "yaml_only": yaml_only,
    }


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
def test_setup_command_preserves_skip_ai_stage_and_output_order(
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_call_command(command_name: str, **_kwargs: object) -> None:
        events.append(command_name)

    def fake_verify(_command: setup_command.Command) -> None:
        events.append("verify")

    monkeypatch.setattr(setup_command, "call_command", fake_call_command)
    monkeypatch.setattr(setup_command.Command, "_verify_setup", fake_verify)

    output = StringIO()
    setup_command.Command(stdout=output).handle(
        **_options(skip_ai_setup=True, yaml_only=True)
    )

    assert events == ["load_base_db_data", "verify"]
    rendered = output.getvalue()
    assert rendered.index("Starting EndoReg DB") < rendered.index("YAML-only mode")
    assert rendered.index("Step 1") < rendered.index("Step 2")
    assert rendered.index("Step 2") < rendered.index("Skipping AI setup")
    assert rendered.index("Skipping AI setup") < rendered.index("Step 6")
    assert rendered.index("Step 6") < rendered.index("completed successfully")


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache"}}
)
def test_setup_command_preserves_full_stage_order_and_flags(
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[object] = []

    def fake_call_command(command_name: str, **_kwargs: object) -> None:
        events.append(command_name)

    def fake_primary_metadata(
        _command: setup_command.Command,
        *,
        force_recreate: bool,
    ) -> bool:
        events.append(("primary_metadata", force_recreate))
        return True

    def fake_metadata_validation(
        _command: setup_command.Command,
        *,
        yaml_only: bool,
    ) -> bool:
        events.append(("metadata_validation", yaml_only))
        return True

    def fake_verify(_command: setup_command.Command) -> None:
        events.append("verify")

    monkeypatch.setattr(setup_command, "call_command", fake_call_command)
    monkeypatch.setattr(
        setup_command.Command,
        "_setup_primary_model_metadata",
        fake_primary_metadata,
    )
    monkeypatch.setattr(
        setup_command.Command,
        "_run_metadata_validation",
        fake_metadata_validation,
    )
    monkeypatch.setattr(setup_command.Command, "_verify_setup", fake_verify)

    setup_command.Command(stdout=StringIO()).handle(
        **_options(force_recreate=True, yaml_only=True)
    )

    assert events == [
        "load_base_db_data",
        "createcachetable",
        "load_ai_model_data",
        "load_ai_model_label_data",
        ("primary_metadata", True),
        ("metadata_validation", True),
        "verify",
    ]


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
def test_setup_command_reports_stage_failure_and_returns_normally(
    monkeypatch: MonkeyPatch,
) -> None:
    service_error = RuntimeError("base unavailable")
    calls: list[str] = []

    def fake_call_command(command_name: str, **_kwargs: object) -> None:
        calls.append(command_name)
        raise service_error

    def fail_verify(_command: setup_command.Command) -> None:
        raise AssertionError("verification must not run")

    monkeypatch.setattr(setup_command, "call_command", fake_call_command)
    monkeypatch.setattr(setup_command.Command, "_verify_setup", fail_verify)

    output = StringIO()
    result = setup_command.Command(stdout=output).handle(**_options(skip_ai_setup=True))

    assert result is None
    assert calls == ["load_base_db_data"]
    assert "❌ Failed to load base data: base unavailable" in output.getvalue()
    assert "Step 2" not in output.getvalue()


def test_setup_command_preserves_strict_option_validation() -> None:
    output = StringIO()

    with pytest.raises(ValidationError):
        setup_command.Command(stdout=output).handle(
            skip_ai_setup="false",
            force_recreate=False,
            yaml_only=False,
        )

    assert output.getvalue() == ""


@pytest.mark.django_db
def test_primary_metadata_force_recreate_preserves_bump_version(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from endoreg_db.models.administration.ai.ai_model import AiModel
    from endoreg_db.utils.setup_config import setup_config

    AiModel.objects.create(name="configured-model")
    weights_path = tmp_path / "weights.safetensors"
    weights_path.write_bytes(b"weights")
    observed: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        setup_config,
        "get_primary_model_name",
        lambda: "configured-model",
    )
    monkeypatch.setattr(
        setup_config,
        "get_primary_labelset_name",
        lambda: "configured-labelset",
    )

    def fake_find_weights(_command: setup_command.Command) -> Path:
        return weights_path

    monkeypatch.setattr(
        setup_command.Command,
        "_find_model_weights_file",
        fake_find_weights,
    )

    def fake_call_command(command_name: str, **kwargs: object) -> None:
        observed.append((command_name, kwargs))

    monkeypatch.setattr(setup_command, "call_command", fake_call_command)

    command = setup_command.Command(stdout=StringIO())
    create_primary_metadata = cast(
        Callable[..., bool],
        getattr(command, "_create_primary_model_metadata"),
    )

    assert create_primary_metadata(force_recreate=True) is True
    assert observed == [
        (
            "create_multilabel_model_meta",
            {
                "model_name": "configured-model",
                "model_meta_version": 1,
                "image_classification_labelset_name": "configured-labelset",
                "model_path": str(weights_path),
                "bump_version": True,
            },
        )
    ]


@dataclass
class _FakeWeights:
    name: str = ""

    def __bool__(self) -> bool:
        return bool(self.name)


@dataclass
class _FakeMetadata:
    events: list[object]
    name: str = "existing-meta"
    version: str = "1.0"
    weights: Any = None

    def __post_init__(self) -> None:
        self.weights = _FakeWeights()

    def save(self, *, update_fields: list[str]) -> None:
        self.events.append(("metadata_save", update_fields))


class _FakeMetadataVersions:
    def __init__(self, metadata: _FakeMetadata) -> None:
        self._metadata = metadata

    def count(self) -> int:
        return 1

    def first(self) -> _FakeMetadata:
        return self._metadata


class _FakeAiModel:
    def __init__(self, metadata: _FakeMetadata, events: list[object]) -> None:
        self.name = "existing-model"
        self.active_meta: object | None = None
        self.metadata_versions = _FakeMetadataVersions(metadata)
        self._events = events

    def save(self) -> None:
        self._events.append("model_save")


def test_existing_metadata_repair_preserves_copy_and_save_order(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from endoreg_db.utils import paths as setup_paths

    events: list[object] = []
    metadata = _FakeMetadata(events)
    model = _FakeAiModel(metadata, events)
    external_weights = tmp_path / "external" / "weights.safetensors"
    storage_dir = tmp_path / "storage"

    monkeypatch.setattr(setup_paths, "STORAGE_DIR", storage_dir)

    def fake_find_weights(_command: setup_command.Command) -> Path:
        return external_weights

    monkeypatch.setattr(
        setup_command.Command,
        "_find_model_weights_file",
        fake_find_weights,
    )

    def fake_copy(*, source: Path, destination: Path) -> None:
        events.append(("copy", source, destination))

    monkeypatch.setattr(setup_command, "atomic_copy_file", fake_copy)

    output = StringIO()
    command = setup_command.Command(stdout=output)
    repair_model_metadata = cast(
        Callable[..., int],
        getattr(command, "_repair_model_metadata"),
    )
    fixed = repair_model_metadata(
        cast(Any, model),
        yaml_only=False,
        context=cast(Any, None),
    )

    destination = storage_dir / "model_weights" / external_weights.name
    assert fixed == 1
    assert events == [
        ("copy", external_weights, destination),
        ("metadata_save", ["weights"]),
        "model_save",
    ]
    assert metadata.weights.name == "model_weights/weights.safetensors"
    assert model.active_meta is metadata
    assert f"      Copied weights to: {destination}" in output.getvalue()
    assert (
        "      Added weights to existing metadata: model_weights/weights.safetensors"
    ) in output.getvalue()


def test_latest_metadata_failure_preserves_output_and_exception_context() -> None:
    source_error = RuntimeError("weights missing")

    class _BrokenModel:
        name = "broken-model"

        @staticmethod
        def get_latest_version() -> object:
            raise source_error

    output = StringIO()
    command = setup_command.Command(stdout=output)
    verify_latest_metadata = cast(
        Callable[[Any], None],
        getattr(command, "_verify_latest_metadata"),
    )

    with pytest.raises(
        Exception,
        match="Model broken-model still has metadata issues: weights missing",
    ) as exc_info:
        verify_latest_metadata(cast(Any, [_BrokenModel()]))

    assert exc_info.value.__context__ is source_error
    assert exc_info.value.__cause__ is None
    assert "  ❌ broken-model: weights missing" in output.getvalue()
