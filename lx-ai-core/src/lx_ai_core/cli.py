from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from lx_ai_core.contracts import InferenceRequest
from lx_ai_core.runtime import run_inference
from lx_ai_core.training import TrainingRequest

LOGGER = logging.getLogger(__name__)


def load_request(path: Path) -> InferenceRequest:
    data = _load_structured_payload(path)
    return InferenceRequest.model_validate(data)


def load_training_request(path: Path) -> TrainingRequest:
    data = _load_structured_payload(path)
    return TrainingRequest.model_validate(data)


def _load_structured_payload(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(handle)
        else:
            data = json.load(handle)
    return data


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        LOGGER.info(
            json.dumps(
                {
                    "event": "lx_ai_core.atomic_json_write",
                    "path": str(path),
                    "tmp_path": tmp_name,
                    "operation": "os.replace",
                }
            )
        )
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _dump_or_write(data: Any, output: Path | None) -> None:
    if output is not None:
        atomic_write_json(output, data)
    else:
        print(json.dumps(data, indent=2))


def validate_request_command(args: argparse.Namespace) -> int:
    request = load_request(args.request)
    _dump_or_write(request.model_dump(mode="json"), args.output)
    return 0


def validate_training_request_command(args: argparse.Namespace) -> int:
    request = load_training_request(args.request)
    _dump_or_write(request.model_dump(mode="json"), args.output)
    return 0


def infer_command(args: argparse.Namespace) -> int:
    request = load_request(args.request)
    result = run_inference(request)
    _dump_or_write(result.model_dump(mode="json"), args.output)
    return 0


def benchmark_command(args: argparse.Namespace) -> int:
    request = load_request(args.request)
    durations: list[float] = []
    last_result = None
    for _ in range(args.runs):
        started = time.perf_counter()
        last_result = run_inference(request)
        durations.append((time.perf_counter() - started) * 1000.0)
    payload = {
        "runs": args.runs,
        "min_ms": min(durations),
        "max_ms": max(durations),
        "mean_ms": statistics.fmean(durations),
        "median_ms": statistics.median(durations),
        "last_result": last_result.model_dump(mode="json") if last_result is not None else None,
    }
    _dump_or_write(payload, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lx-ai-core")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-request")
    validate.add_argument("request", type=Path)
    validate.add_argument("--output", type=Path)
    validate.set_defaults(func=validate_request_command)

    validate_training = subparsers.add_parser("validate-training-request")
    validate_training.add_argument("request", type=Path)
    validate_training.add_argument("--output", type=Path)
    validate_training.set_defaults(func=validate_training_request_command)

    infer = subparsers.add_parser("infer")
    infer.add_argument("request", type=Path)
    infer.add_argument("--output", type=Path)
    infer.set_defaults(func=infer_command)

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("request", type=Path)
    benchmark.add_argument("--runs", type=int, default=10)
    benchmark.add_argument("--output", type=Path)
    benchmark.set_defaults(func=benchmark_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
