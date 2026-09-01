from typing import Protocol, cast

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile


class _CenterCarrier(Protocol):
    center: Center | None


class _NamedCenter(Protocol):
    name: str


def ensure_center(instance: RawPdfFile | VideoFile, center: str | None) -> Center:
    center_carrier = cast(_CenterCarrier, instance)
    instance_center = center_carrier.center
    if instance_center is None:
        raise AssertionError
    named_center = cast(_NamedCenter, instance_center)
    if center is not None and named_center.name != center:
        raise AssertionError
    return instance_center
