from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class SegmentAnnotationMetadataInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    segment_id: int | None = Field(default=None, alias="segmentId")


class SegmentAnnotationInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    annotation_type: str = Field(alias="type")
    video_id: int = Field(alias="videoId", gt=0)
    start_time: float = Field(alias="startTime", ge=0)
    end_time: float = Field(alias="endTime", ge=0)
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: SegmentAnnotationMetadataInput = Field(
        default_factory=SegmentAnnotationMetadataInput
    )

    @model_validator(mode="after")
    def validate_segment_annotation(self) -> "SegmentAnnotationInput":
        if self.annotation_type != "segment":
            raise ValueError("annotation type must be 'segment'")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self

    def to_frame_range(self, fps: float) -> tuple[int, int]:
        return (
            int(round(self.start_time * fps)),
            int(round(self.end_time * fps)),
        )


def parse_segment_annotation_input(
    annotation: SegmentAnnotationInput | dict[str, Any],
) -> SegmentAnnotationInput | None:
    if isinstance(annotation, SegmentAnnotationInput):
        return annotation

    try:
        return SegmentAnnotationInput.model_validate(annotation)
    except ValidationError:
        return None
