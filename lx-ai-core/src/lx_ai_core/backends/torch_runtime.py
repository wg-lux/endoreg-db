from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any

from lx_ai_core.contracts import (
    BackendName,
    InferenceInput,
    InferenceRequest,
    InferenceResult,
    MaskArtifact,
    Modality,
    RunMetrics,
    Score,
    ScoreVector,
    TaskKind,
)
from lx_ai_core.postprocessing import (
    mask_rle_encode_flat,
    multilabel_uncertainty_scores,
    temporal_segments_from_scores,
)
from lx_ai_core.runtime import ModelCache, ModelLoadError, UnsupportedTaskError
from lx_ai_core.temporal import (
    binary_markov_smooth_scores,
    state_path_to_segments,
    viterbi_decode_state_scores,
)


class TorchRuntime:
    backend_name = BackendName.TORCH

    def __init__(
        self,
        model_cache: ModelCache | None = None,
        *,
        max_cached_models: int | None = None,
    ) -> None:
        self.model_cache = model_cache or ModelCache(max_items=max_cached_models)

    def infer(self, request: InferenceRequest) -> InferenceResult:
        try:
            import torch  # noqa: F401
        except ModuleNotFoundError as exc:
            raise ModelLoadError(
                "TorchRuntime requires the optional dependency extra: lx-ai-core[torch]"
            ) from exc

        started = time.perf_counter()
        device = self._resolve_device(request)
        dtype = self._resolve_dtype(request, device)
        task = request.model_spec.task_kind

        if task == TaskKind.TEMPORAL_MULTILABEL_SEGMENTATION:
            result = self._infer_temporal(request=request, device=device, dtype=dtype)
        elif task in {
            TaskKind.MULTILABEL_CLASSIFICATION,
            TaskKind.SEMANTIC_SEGMENTATION,
            TaskKind.SIGNAL_CLASSIFICATION,
        }:
            model = self._get_prepared_model(request, device=device, dtype=dtype)
            tensor = self._input_to_tensor(
                request.inputs,
                request.model_spec.modality,
                device,
                dtype,
            )
            with torch.inference_mode():
                output = model(tensor)
            if task in {TaskKind.MULTILABEL_CLASSIFICATION, TaskKind.SIGNAL_CLASSIFICATION}:
                result = self._classification_result(request, output, device)
            else:
                result = self._segmentation_result(request, output, device)
        else:
            raise UnsupportedTaskError(
                f"TorchRuntime does not implement task_kind={task.value!r}"
            )

        duration_ms = (time.perf_counter() - started) * 1000.0
        result.duration_ms = duration_ms
        result.metrics = RunMetrics(
            duration_ms=duration_ms,
            input_count=self._input_count(request.inputs),
            backend=self.backend_name.value,
            device=str(device),
        )
        return result

    def _resolve_device(self, request: InferenceRequest) -> Any:
        import torch

        requested = str(request.options.get("device") or request.model_spec.device)
        if requested == "auto":
            requested = "cuda" if torch.cuda.is_available() else "cpu"
        if requested == "cuda" and not torch.cuda.is_available():
            requested = "cpu"
        return torch.device(requested)

    def _resolve_dtype(self, request: InferenceRequest, device: Any) -> Any:
        import torch

        dtype_name = str(request.options.get("dtype") or request.model_spec.dtype)
        if dtype_name == "float16" and device.type == "cuda":
            return torch.float16
        if dtype_name == "bfloat16" and device.type == "cuda":
            return torch.bfloat16
        return torch.float32

    def _get_prepared_model(self, request: InferenceRequest, *, device: Any, dtype: Any) -> Any:
        cache_key = self._runtime_cache_key(request, device=device, dtype=dtype)

        def loader(spec: Any) -> Any:
            model = self._load_model(spec)
            model.to(device=device, dtype=dtype)
            model.eval()
            return model

        return self.model_cache.get_or_load(
            request.model_spec,
            loader,
            cache_key=cache_key,
        )

    def _runtime_cache_key(self, request: InferenceRequest, *, device: Any, dtype: Any) -> str:
        return f"{request.model_spec.cache_key}|runtime_device={device}|runtime_dtype={dtype}"

    def _load_model(self, spec: Any) -> Any:
        import torch
        from torch import nn

        model = None
        if spec.entrypoint:
            target = self._import_entrypoint(spec.entrypoint)
            model = target() if callable(target) and not isinstance(target, nn.Module) else target
            if not isinstance(model, nn.Module):
                raise ModelLoadError(
                    f"entrypoint {spec.entrypoint!r} did not produce torch.nn.Module"
                )

        if spec.artifact_path:
            artifact = Path(spec.artifact_path)
            if not artifact.is_file():
                raise ModelLoadError(f"model artifact does not exist: {artifact}")
            loaded = torch.load(artifact, map_location="cpu")
            if isinstance(loaded, nn.Module):
                model = loaded
            elif isinstance(loaded, dict) and model is not None:
                state = loaded.get("state_dict", loaded)
                model.load_state_dict(state)
            elif model is None:
                raise ModelLoadError(
                    "artifact is a state dict; provide model_spec.entrypoint to construct the model"
                )

        if model is None:
            raise ModelLoadError("model_spec requires entrypoint or artifact_path")
        return model

    def _import_entrypoint(self, entrypoint: str) -> Any:
        if ":" not in entrypoint:
            raise ModelLoadError("entrypoint must use module:attribute syntax")
        module_name, attr_name = entrypoint.split(":", 1)
        module = importlib.import_module(module_name)
        target: Any = module
        for part in attr_name.split("."):
            target = getattr(target, part)
        return target

    def _input_to_tensor(
        self,
        inputs: InferenceInput,
        modality: Modality,
        device: Any,
        dtype: Any,
    ) -> Any:
        import torch

        if inputs.array is not None:
            tensor = torch.as_tensor(inputs.array, dtype=dtype)
        elif inputs.path is not None:
            tensor = self._image_path_to_tensor(inputs.path)
        elif inputs.paths:
            tensors = [self._image_path_to_tensor(path).squeeze(0) for path in inputs.paths]
            tensor = torch.stack(tensors, dim=0)
        else:
            raise ValueError(f"no tensor-compatible input for modality={modality.value!r}")

        if modality == Modality.FRAME:
            tensor = self._normalize_frame_tensor(tensor)
        elif modality == Modality.SIGNAL:
            if tensor.ndim == 1:
                tensor = tensor.unsqueeze(0)
        return tensor.to(device=device, dtype=dtype, non_blocking=True)

    def _image_path_to_tensor(self, path: Path) -> Any:
        import torch
        import numpy as np
        from PIL import Image

        if not path.is_file():
            raise FileNotFoundError(f"input image does not exist: {path}")
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        arr = np.asarray(image, dtype=np.float32) / np.float32(255.0)
        tensor = torch.from_numpy(arr).permute(2, 0, 1).contiguous()
        return tensor.unsqueeze(0)

    def _normalize_frame_tensor(self, tensor: Any) -> Any:
        if tensor.ndim == 2:
            return tensor.unsqueeze(0).unsqueeze(0)
        if tensor.ndim == 3:
            # HWC input from JSON arrays is common; CHW is left unchanged.
            if tensor.shape[-1] in (1, 3):
                tensor = tensor.permute(2, 0, 1).contiguous()
            return tensor.unsqueeze(0)
        if tensor.ndim == 4:
            if tensor.shape[-1] in (1, 3):
                tensor = tensor.permute(0, 3, 1, 2).contiguous()
            return tensor
        raise ValueError("frame tensor must be 2D, 3D, or 4D")

    def _classification_result(self, request: InferenceRequest, output: Any, device: Any) -> InferenceResult:
        import torch

        logits = output.detach()
        if logits.ndim == 1:
            logits = logits.unsqueeze(0)
        probabilities = torch.sigmoid(logits).to("cpu")
        labels = self._labels_for_width(request, int(probabilities.shape[1]))
        score_vectors = []
        probability_rows = probabilities.tolist()
        for index, row in enumerate(probability_rows):
            scores = [
                Score(label=labels[column], score=float(row[column]))
                for column in range(len(labels))
            ]
            score_vectors.append(ScoreVector(index=index, scores=scores))
        return self._base_result(
            request=request,
            device=device,
            score_vectors=score_vectors,
            raw_output={
                "shape": list(output.shape),
                "uncertainty": multilabel_uncertainty_scores(probability_rows),
            },
        )

    def _segmentation_result(self, request: InferenceRequest, output: Any, device: Any) -> InferenceResult:
        import torch

        logits = output.detach()
        if logits.ndim == 3:
            logits = logits.unsqueeze(0)
        if logits.ndim != 4:
            raise ValueError("semantic segmentation output must have shape [B, C, H, W]")
        probs = torch.softmax(logits, dim=1).to("cpu")
        masks = torch.argmax(probs, dim=1)
        labels = self._labels_for_width(request, int(logits.shape[1]))
        artifacts: list[MaskArtifact] = []
        for batch_index, mask_tensor in enumerate(masks):
            for class_index in sorted(int(value.item()) for value in torch.unique(mask_tensor)):
                binary_mask = (mask_tensor == class_index).to(dtype=torch.int64)
                shape = [int(size) for size in binary_mask.shape]
                flat_mask = binary_mask.reshape(-1).tolist()
                counts, shape = mask_rle_encode_flat(flat_mask, shape)
                class_label = labels[class_index] if class_index < len(labels) else str(class_index)
                class_prob = probs[batch_index, class_index]
                score = float(class_prob[binary_mask.bool()].mean().item())
                artifacts.append(
                    MaskArtifact(
                        shape=shape,
                        counts=counts,
                        class_label=class_label,
                        score=score,
                    )
                )
        return self._base_result(
            request=request,
            device=device,
            masks=artifacts,
            raw_output={"shape": list(output.shape)},
        )

    def _infer_temporal(self, request: InferenceRequest, device: Any, dtype: Any) -> InferenceResult:
        import torch

        if request.inputs.frame_scores is not None:
            frame_scores = request.inputs.frame_scores
        elif request.inputs.array is not None:
            frame_scores = [[float(value) for value in row] for row in request.inputs.array]
        elif request.inputs.paths:
            model = self._get_prepared_model(request, device=device, dtype=dtype)
            tensor = self._input_to_tensor(request.inputs, Modality.FRAME, device, dtype)
            with torch.inference_mode():
                output = model(tensor)
            probs = torch.sigmoid(output.detach()).to("cpu")
            frame_scores = [
                [float(probs[row, column].item()) for column in range(probs.shape[1])]
                for row in range(probs.shape[0])
            ]
        else:
            raise ValueError("temporal inference requires frame_scores, array, or frame paths")

        width = len(frame_scores[0]) if frame_scores else 0
        labels = self._labels_for_width(request, width)
        temporal_model = str(request.options.get("temporal_model", "hysteresis")).lower()
        awareness: dict[str, Any] | None = None
        scores_for_output = frame_scores

        if temporal_model == "markov":
            scores_for_output, awareness = binary_markov_smooth_scores(
                frame_scores,
                labels,
                stay_probability=request.options.get("markov_stay_probability", 0.96),
                enter_probability=request.options.get("markov_enter_probability", 0.02),
                label_priors=request.options.get("markov_label_priors"),
                change_scores=request.options.get("change_scores"),
                change_sensitivity=float(request.options.get("markov_change_sensitivity", 0.0)),
                diffusion_target=float(request.options.get("markov_diffusion_target", 0.5)),
            )

        if temporal_model == "viterbi":
            path, confidences, awareness = viterbi_decode_state_scores(
                frame_scores,
                labels,
                transition_matrix=request.options.get("transition_matrix"),
                stay_probability=float(request.options.get("state_stay_probability", 0.96)),
                initial_distribution=request.options.get("initial_distribution"),
            )
            segments = state_path_to_segments(path, confidences, labels)
        else:
            if temporal_model not in {"hysteresis", "markov"}:
                raise UnsupportedTaskError(f"unknown temporal_model={temporal_model!r}")
            segments = temporal_segments_from_scores(
                scores_for_output,
                labels,
                threshold=float(request.options.get("threshold", 0.5)),
                thresholds=request.options.get("thresholds"),
                low_threshold=request.options.get("low_threshold"),
                low_thresholds=request.options.get("low_thresholds"),
                min_length=int(request.options.get("min_length", 1)),
                max_gap=int(request.options.get("max_gap", 0)),
                smoothing_window=int(request.options.get("smoothing_window", 1)),
            )
        include_score_vectors = bool(request.options.get("include_score_vectors", True))
        score_vectors = (
            [
                ScoreVector(
                    index=index,
                    scores=[
                        Score(label=labels[column], score=float(value))
                        for column, value in enumerate(row)
                    ],
                )
                for index, row in enumerate(scores_for_output)
            ]
            if include_score_vectors
            else []
        )
        return self._base_result(
            request=request,
            device=device,
            temporal_segments=segments,
            score_vectors=score_vectors,
            raw_output={
                "frames": len(frame_scores),
                "labels": labels,
                "temporal_awareness": awareness,
                "uncertainty": multilabel_uncertainty_scores(frame_scores)
                if bool(request.options.get("include_uncertainty", False))
                else None,
            },
        )

    def _labels_for_width(self, request: InferenceRequest, width: int) -> list[str]:
        labels = list(request.model_spec.labels)
        if len(labels) < width:
            labels.extend([f"class_{index}" for index in range(len(labels), width)])
        return labels[:width]

    def _base_result(
        self,
        *,
        request: InferenceRequest,
        device: Any,
        score_vectors: list[ScoreVector] | None = None,
        temporal_segments: list[Any] | None = None,
        masks: list[MaskArtifact] | None = None,
        raw_output: dict[str, Any] | None = None,
    ) -> InferenceResult:
        return InferenceResult(
            model_spec=request.model_spec,
            backend=self.backend_name.value,
            device=str(device),
            duration_ms=0.0,
            provenance={
                "request_id": request.request_id,
                "runtime": "torch",
                "local_only": True,
            },
            score_vectors=score_vectors or [],
            temporal_segments=temporal_segments or [],
            masks=masks or [],
            raw_output=raw_output,
        )

    def _input_count(self, inputs: InferenceInput) -> int:
        if inputs.paths:
            return len(inputs.paths)
        if inputs.frame_scores is not None:
            return len(inputs.frame_scores)
        if inputs.array is not None and isinstance(inputs.array, list):
            return max(1, len(inputs.array))
        return 1
