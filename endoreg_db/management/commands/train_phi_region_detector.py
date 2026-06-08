from __future__ import annotations

import json
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.management_command import (
    TrainPhiRegionDetectorCommandOptionsPayload,
    validate_model_training_result,
)


class PhiRegionDetectorTrainingConfigProtocol(Protocol):
    dataset_yaml: Path
    base_model: str
    epochs: int
    batch_size: int
    input_size: int


class Command(BaseCommand):
    help = "Train the lx-anonymizer PHI-region detector and export an ONNX artifact."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dataset-yaml", type=Path, required=True)
        parser.add_argument("--output-dir", type=Path, required=True)
        parser.add_argument("--base-model", type=str, default="yolov8n.pt")
        parser.add_argument("--run-name", type=str, default=None)
        parser.add_argument("--epochs", type=int, default=50)
        parser.add_argument("--batch-size", type=int, default=16)
        parser.add_argument("--input-size", type=int, default=640)
        parser.add_argument("--device", type=str, default="auto")
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--patience", type=int, default=25)
        parser.add_argument("--confidence-threshold", type=float, default=0.35)
        parser.add_argument("--nms-threshold", type=float, default=0.45)
        parser.add_argument("--class-ids", type=str, default="")
        parser.add_argument(
            "--skip-onnx-export",
            dest="export_onnx",
            action="store_false",
            help="Keep the PyTorch checkpoint as the primary artifact.",
        )
        parser.set_defaults(export_onnx=True)

    def handle(self, *args: object, **options: object) -> None:
        try:
            options_payload = TrainPhiRegionDetectorCommandOptionsPayload.model_validate(
                options
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        try:
            training_module = import_module(
                "lx_anonymizer.text_detection.phi_region_detector_training"
            )
        except ImportError as exc:
            raise CommandError(
                "lx-anonymizer PHI detector training is not available. Install "
                "lx-anonymizer with its training extra before starting this run."
            ) from exc

        config_factory_candidate: object = getattr(
            training_module, "PhiRegionDetectorTrainingConfig"
        )
        train_candidate: object = getattr(training_module, "train_phi_region_detector")
        if not callable(config_factory_candidate):
            raise CommandError("PhiRegionDetectorTrainingConfig is not callable.")
        if not callable(train_candidate):
            raise CommandError("train_phi_region_detector is not callable.")

        config_factory = cast(
            Callable[..., PhiRegionDetectorTrainingConfigProtocol],
            config_factory_candidate,
        )
        train_model = cast(
            Callable[[PhiRegionDetectorTrainingConfigProtocol], JsonObject],
            train_candidate,
        )

        config = config_factory(
            dataset_yaml=options_payload.dataset_yaml,
            output_dir=options_payload.output_dir,
            base_model=options_payload.base_model,
            run_name=options_payload.run_name or None,
            epochs=options_payload.epochs,
            batch_size=options_payload.batch_size,
            input_size=options_payload.input_size,
            device=options_payload.device,
            workers=options_payload.workers,
            patience=options_payload.patience,
            export_onnx=options_payload.export_onnx,
            confidence_threshold=options_payload.confidence_threshold,
            nms_threshold=options_payload.nms_threshold,
            class_ids=options_payload.class_ids,
        )

        self.stdout.write(
            self.style.NOTICE(
                "Training lx-anonymizer PHI-region detector: "
                f"dataset_yaml={config.dataset_yaml}, "
                f"base_model={config.base_model!r}, "
                f"epochs={config.epochs}, "
                f"batch_size={config.batch_size}, "
                f"input_size={config.input_size}"
            )
        )
        result = validate_model_training_result(train_model(config))
        self.stdout.write(
            self.style.SUCCESS(
                "PHI-region detector training completed. "
                f"Model saved to: {result.model_path}"
            )
        )
        self.stdout.write(json.dumps(result.model_dump(mode="json")))
