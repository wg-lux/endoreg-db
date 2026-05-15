from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .inference_dataset import InferenceDataset
    from .multilabel_classification_net import MultiLabelClassificationNet
    from .predict import Classifier

__all__ = [
    "InferenceDataset",
    "MultiLabelClassificationNet",
    "Classifier",
]


def __getattr__(name: str) -> Any:
    if name == "InferenceDataset":
        from .inference_dataset import InferenceDataset

        globals()[name] = InferenceDataset
        return InferenceDataset
    if name == "MultiLabelClassificationNet":
        from .multilabel_classification_net import MultiLabelClassificationNet

        globals()[name] = MultiLabelClassificationNet
        return MultiLabelClassificationNet
    if name == "Classifier":
        from .predict import Classifier

        globals()[name] = Classifier
        return Classifier
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
