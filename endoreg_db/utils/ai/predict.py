from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Callable, cast

import numpy as np
import torch
from icecream import ic
from torch import nn
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm

from endoreg_db.utils.file_operations import atomic_write_file
from lx_dtypes.models.contracts.ai_prediction import (
    AiPredictionConfigPayload,
    AiPredictionPostProcessPayload,
    AiPredictionResultPayload,
    AiPredictionSequencePayload,
    AiPredictionSerializablePostProcessPayload,
    to_json_path,
)

from .inference_dataset import InferenceDataset
from .postprocess import concat_pred_dicts, find_true_pred_sequences, make_smooth_preds

ConcatPredDicts = Callable[[list[dict[str, list[float]]]], dict[str, list[float]]]
MakeSmoothPreds = Callable[[list[float], int, int], np.ndarray]
FindTruePredSequences = Callable[[np.ndarray], list[tuple[int, int]]]

concat_pred_dicts_typed = cast(ConcatPredDicts, concat_pred_dicts)
make_smooth_preds_typed = cast(MakeSmoothPreds, make_smooth_preds)
find_true_pred_sequences_typed = cast(FindTruePredSequences, find_true_pred_sequences)

sample_config = AiPredictionConfigPayload(
    mean=(0.45211223, 0.27139644, 0.19264949),
    std=(0.31418097, 0.21088019, 0.16059452),
    size_x=716,
    size_y=716,
    axes=[2, 0, 1],
    batchsize=16,
    num_workers=0,
    labels=[
        "appendix",
        "blood",
        "diverticule",
        "grasper",
        "ileocaecalvalve",
        "ileum",
        "low_quality",
        "nbi",
        "needle",
        "outside",
        "polyp",
        "snare",
        "water_jet",
        "wound",
    ],
)
activation = nn.Sigmoid()


class Classifier:
    def __init__(
        self,
        model: nn.Module | None = None,
        config: AiPredictionConfigPayload | None = None,
        verbose: bool = False,
    ) -> None:
        self.config = config or sample_config
        self.model = model
        self.verbose = verbose

    def pipe(
        self,
        paths: Sequence[str],
        crops: Sequence[object],
        verbose: bool | None = None,
    ) -> list[list[float]]:
        if verbose is None:
            verbose = self.verbose

        dataset = InferenceDataset(paths, crops, self.config.model_dump(mode="python"))
        if verbose:
            ic("Dataset created")

        use_cuda = torch.cuda.is_available()
        dataset_typed = cast(Dataset[torch.Tensor], dataset)
        dl: DataLoader[torch.Tensor] = DataLoader(
            dataset=dataset_typed,
            batch_size=self.config.batchsize,
            num_workers=self.config.num_workers,
            shuffle=False,
            pin_memory=use_cuda,
        )
        if verbose:
            ic("Dataloader created")

        predictions: list[list[float]] = []
        with torch.inference_mode():
            if self.verbose:
                ic("Starting inference")
            if self.model is None:
                raise ValueError("Model is not loaded")

            try:
                device = next(self.model.parameters()).device
                if verbose:
                    print(f"Using device: {device}")
            except StopIteration:
                device = torch.device("cpu")
                if verbose:
                    print("Model has no parameters, defaulting to CPU")
            except Exception as exc:
                device = torch.device("cpu")
                if verbose:
                    print(f"Device detection failed, using CPU: {exc}")

            self.model.eval()
            for batch in tqdm(dl):
                batch = batch.to(device, non_blocking=True)
                prediction = self.model(batch)
                prediction = activation(prediction).cpu().tolist()
                predictions += prediction

        return predictions

    def __call__(self, image: object, crop: object = None) -> list[list[float]]:
        return self.pipe([str(image)], [crop])

    def readable(self, predictions: Sequence[float]) -> dict[str, float]:
        return {
            label: float(prediction)
            for label, prediction in zip(self.config.labels, predictions, strict=True)
        }

    def get_prediction_dict(
        self, predictions: list[list[float]], paths: Sequence[str]
    ) -> AiPredictionResultPayload:
        return AiPredictionResultPayload(
            labels=self.config.labels,
            paths=[to_json_path(path) for path in paths],
            predictions=predictions,
        )

    def get_prediction_json(
        self,
        predictions: list[list[float]],
        paths: Sequence[str],
        json_target_path: str | None = None,
    ) -> None:
        target_path = json_target_path or "predictions.json"
        json_dict = self.get_prediction_dict(predictions, paths).model_dump(mode="json")
        atomic_write_file(
            destination=Path(target_path),
            content=[json.dumps(json_dict).encode("utf-8")],
        )
        if self.verbose:
            ic(f"Saved predictions to {target_path}")

    def post_process_predictions(
        self,
        pred_dicts: list[dict[str, list[float]]],
        window_size_s: int = 1,
        fps: int = 50,
        min_seq_len_s: float = 0.5,
    ) -> AiPredictionPostProcessPayload:
        predictions = concat_pred_dicts_typed(pred_dicts)
        smooth_predictions: dict[str, list[float]] = {}
        for key in predictions:
            smooth_predictions[key] = np.asarray(
                make_smooth_preds_typed(predictions[key], window_size_s, fps),
                dtype=float,
            ).tolist()

        binary_predictions: dict[str, list[bool]] = {}
        for key, values in smooth_predictions.items():
            binary_predictions[key] = [p > 0.5 for p in values]

        raw_sequences: dict[str, list[AiPredictionSequencePayload]] = {}
        for key, values in binary_predictions.items():
            raw_sequences[key] = [
                AiPredictionSequencePayload(start=start, stop=stop)
                for start, stop in find_true_pred_sequences_typed(np.array(values))
            ]

        filtered_sequences: dict[str, list[AiPredictionSequencePayload]] = {}
        min_seq_len = int(min_seq_len_s * fps)
        for key, sequences in raw_sequences.items():
            filtered_sequences[key] = [
                sequence
                for sequence in sequences
                if sequence.stop - sequence.start > min_seq_len
            ]

        return AiPredictionPostProcessPayload(
            predictions=predictions,
            smooth_predictions=smooth_predictions,
            binary_predictions=binary_predictions,
            raw_sequences=raw_sequences,
            filtered_sequences=filtered_sequences,
        )

    def post_process_predictions_serializable(
        self,
        pred_dicts: list[dict[str, list[float]]],
        window_size_s: int = 1,
        fps: int = 50,
        min_seq_len_s: float = 0.5,
    ) -> AiPredictionSerializablePostProcessPayload:
        result = self.post_process_predictions(
            pred_dicts, window_size_s, fps, min_seq_len_s
        )

        def _split_sequences(
            sequences: list[AiPredictionSequencePayload],
        ) -> dict[str, list[int]]:
            return {
                "start": [sequence.start for sequence in sequences],
                "stop": [sequence.stop for sequence in sequences],
            }

        return AiPredictionSerializablePostProcessPayload(
            predictions=result.predictions,
            smooth_predictions=result.smooth_predictions,
            binary_predictions=result.binary_predictions,
            raw_sequences={
                key: _split_sequences(value)
                for key, value in result.raw_sequences.items()
            },
            filtered_sequences={
                key: _split_sequences(value)
                for key, value in result.filtered_sequences.items()
            },
        )
