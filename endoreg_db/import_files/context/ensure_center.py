from typing import Union, Optional

from endoreg_db.models import Center
from endoreg_db.models.media import RawPdfFile, VideoFile


def ensure_center(
    instance: Union[RawPdfFile, VideoFile], center: Optional[str]
) -> Center:
    if not isinstance(instance.center, Center):
        raise AssertionError
    if not isinstance(instance.center.name, str):
        raise AssertionError
    assert isinstance(instance.center.get_by_name(center), Center)
    if not instance.center.get_by_name(center).name == instance.center.name:
        raise AssertionError
    return instance.center
