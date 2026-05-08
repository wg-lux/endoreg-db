from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

try:
    from lx_anonymizer.text_detection.phi_region_detector_training import (
        PhiRegionDetectorTrainingConfig,
        train_phi_region_detector,
    )
except ImportError:
    PhiRegionDetectorTrainingConfig = None  # type: ignore[assignment]
    train_phi_region_detector = None  # type: ignore[assignment]


class Command(BaseCommand):
    help = "Train the lx-anonymizer PHI-region detector and export an ONNX artifact."

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        if PhiRegionDetectorTrainingConfig is None or train_phi_region_detector is None:
            raise CommandError(
                "lx-anonymizer PHI detector training is not available. Install "
                "lx-anonymizer with its training extra before starting this run."
            )

        config = PhiRegionDetectorTrainingConfig(
            dataset_yaml=options["dataset_yaml"],
            output_dir=options["output_dir"],
            base_model=str(options["base_model"]).strip(),
            run_name=options["run_name"],
            epochs=int(options["epochs"]),
            batch_size=int(options["batch_size"]),
            input_size=int(options["input_size"]),
            device=str(options["device"]).strip() or "auto",
            workers=int(options["workers"]),
            patience=int(options["patience"]),
            export_onnx=bool(options["export_onnx"]),
            confidence_threshold=float(options["confidence_threshold"]),
            nms_threshold=float(options["nms_threshold"]),
            class_ids=str(options["class_ids"] or "").strip(),
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
        result = train_phi_region_detector(config)
        self.stdout.write(
            self.style.SUCCESS(
                "PHI-region detector training completed. "
                f"Model saved to: {result['model_path']}"
            )
        )
        self.stdout.write(json.dumps(result))
        return None
