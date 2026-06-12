from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypedDict, cast
from django.db.models import F, Window
from django.db.models.functions import RowNumber
from tqdm import tqdm

from ...models import ImageClassificationAnnotation, LabelSet


class LegacyAnnotationEntry(TypedDict):
    frame: object
    annotations: list["LegacyLabelValueEntry"]


class LegacyLabelValueEntry(TypedDict):
    label: str
    value: int


class LegacyDatasetFrameEntry(TypedDict):
    path: str
    labels: list[int]


class LegacyAnnotationBucket(TypedDict):
    frame: object
    annotations: list[LegacyLabelValueEntry]


class _FramePathLike(Protocol):
    pk: int

    @property
    def file_path(self) -> Path: ...


def _labelset_or_raise(labelset_name: str, version: int | None) -> LabelSet:
    if version is not None:
        return LabelSet.objects.get(name=labelset_name, version=version)

    labelset = LabelSet.objects.filter(name=labelset_name).order_by("-version").first()
    if labelset is None:
        raise ValueError(f"No label set found with the name: {labelset_name}")
    return labelset


def get_legacy_annotations_for_labelset(
    labelset_name: str,
    version: int | None = None,
) -> list[LegacyAnnotationEntry]:
    labelset = _labelset_or_raise(labelset_name, version)
    labels_in_set = labelset.labels.all()

    annotations = (
        ImageClassificationAnnotation.objects.filter(label__in=labels_in_set)
        .select_related("frame", "label")
        .annotate(
            latest_annotation=Window(
                expression=RowNumber(),
                partition_by=[F("frame"), F("label")],
                order_by=F("date_modified").desc(),
            )
        )
        .filter(latest_annotation=1)
    )

    organized_annotations_dict: dict[int, LegacyAnnotationBucket] = {}
    for annotation in tqdm(annotations):
        frame = cast(_FramePathLike, annotation.frame)
        frame_id = int(frame.pk)
        entry = organized_annotations_dict.get(frame_id)
        if entry is None:
            annotations_list: list[LegacyLabelValueEntry] = []
            entry = cast(
                LegacyAnnotationBucket,
                {"frame": frame, "annotations": annotations_list},
            )
            organized_annotations_dict[frame_id] = entry
        annotations_list = entry["annotations"]
        annotations_list.append(
            {"label": annotation.label.name, "value": int(annotation.value)}
        )

    return list(organized_annotations_dict.values())


def generate_legacy_dataset_output(
    labelset_name: str,
    version: int | None = None,
) -> tuple[list[LegacyDatasetFrameEntry], LabelSet]:
    organized_annotations = get_legacy_annotations_for_labelset(
        labelset_name,
        version,
    )
    labelset = _labelset_or_raise(labelset_name, version)
    all_labels = labelset.get_labels_in_order()

    dataset_output: list[LegacyDatasetFrameEntry] = []
    for entry in organized_annotations:
        frame = cast(_FramePathLike, entry["frame"])
        frame_data: LegacyDatasetFrameEntry = {
            "path": str(frame.file_path),
            "labels": [-1] * len(all_labels),
        }

        for annotation in entry["annotations"]:
            for index, label in enumerate(all_labels):
                if label.name == annotation["label"]:
                    frame_data["labels"][index] = int(annotation["value"])
                    break

        dataset_output.append(frame_data)

    return dataset_output, labelset
