"""Read validated processed training frames without requiring a raw-video cache."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from io import BytesIO
from typing import TYPE_CHECKING, cast

from lx_ai_core.training import (
    FrameByteStream,
    ProcessedFrameReference,
    TrainingDatasetManifest,
)

from PIL import Image

from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.utils.encryption.storage_materialization import (
    materialized_plaintext_field_file,
)
from endoreg_db.utils.frame_stream import read_video_path_frame_jpeg

if TYPE_CHECKING:
    from lx_ai_core.backends.torch_training import StreamedFrameDataset
    from torch import Tensor


def validate_processed_training_frame(frame: Frame) -> None:
    """Require reviewed processed media without reading pixels or touching raw media."""
    video = frame.video
    state = video.state
    metadata: object = video.meta
    integrity_status = (
        str(cast(Mapping[str, object], metadata).get("integrity_status", "")).lower()
        if isinstance(metadata, Mapping)
        else ""
    )
    if (
        state is None
        or not state.anonymized
        or not state.anonymization_validated
        or not state.segment_annotations_validated
        or not state.outside_segments_removed
        or not state.ready_for_export
        or state.processing_error
        or integrity_status == "lost"
        or not video.processed_file.name
    ):
        raise ValueError("Training requires validated, export-ready processed video.")


def read_processed_training_frame_bytes(frame: Frame) -> bytes:
    """Decode the persisted frame identity and finish protected cleanup before return."""
    validate_processed_training_frame(frame)
    video = frame.video
    with materialized_plaintext_field_file(video.processed_file, suffix=".mp4") as path:
        sample = read_video_path_frame_jpeg(
            path,
            frame_number=frame.frame_number,
            timestamp=frame.timestamp,
        )
    return sample.image_bytes


def read_processed_training_image(frame: Frame) -> Image.Image:
    with BytesIO(read_processed_training_frame_bytes(frame)) as encoded:
        with Image.open(encoded) as image:
            return image.convert("RGB")


class ProcessedTrainingFrameProvider:
    """Local training service adapter scoped to independently authorized frame IDs.

    Callers supply IDs from their authorized dataset, never from an untrusted
    manifest alone. Every read rechecks current frame membership and media state.
    No database connections or decrypted artifacts are retained across reads.
    """

    def __init__(self, *, allowed_frame_ids: Sequence[int]) -> None:
        if not allowed_frame_ids or any(
            type(pk) is not int or pk < 1 for pk in allowed_frame_ids
        ):
            raise ValueError("allowed_frame_ids must contain positive frame identities")
        self._allowed_frame_ids = tuple(allowed_frame_ids)

    @contextmanager
    def open_frame(
        self, reference: ProcessedFrameReference
    ) -> Generator[FrameByteStream]:
        reference = ProcessedFrameReference.model_validate(reference.model_dump())
        frame = Frame.objects.select_related("video__state").get(
            pk__in=self._allowed_frame_ids,
            video_id=reference.video_id,
            frame_number=reference.frame_number,
        )
        with BytesIO(read_processed_training_frame_bytes(frame)) as stream:
            yield stream


def streamed_training_dataset(
    manifest: TrainingDatasetManifest,
    *,
    allowed_frame_ids: Sequence[int],
    transform: Callable[["Tensor"], "Tensor"] | None = None,
) -> "StreamedFrameDataset":
    """Bind the core loader to an authorized local training selection."""
    from lx_ai_core.backends.torch_training import StreamedFrameDataset

    return StreamedFrameDataset(
        manifest,
        ProcessedTrainingFrameProvider(allowed_frame_ids=allowed_frame_ids),
        transform=transform,
    )
