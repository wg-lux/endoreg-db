from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lx_dtypes.models.contracts.endoscopy_processor import (
    RoiBoxCore,
    roi_box_or_none_from_object,
)
from lx_dtypes.models.contracts.json_types import JsonValue


def validate_endo_roi(
    endo_roi: object | None = None,
    *,
    endo_roi_dict: object | None = None,
) -> bool:
    """
    Validate an endoscope ROI using the shared RoiBoxCore contract.

    `endo_roi_dict` remains as a compatibility keyword for older callers, but it
    is normalized into RoiBoxCore instead of handled as a loose dict.
    """
    raw_roi = endo_roi if endo_roi is not None else endo_roi_dict
    if raw_roi is not None and not isinstance(raw_roi, (RoiBoxCore, Mapping)):
        return False
    roi = roi_box_or_none_from_object(
        cast(RoiBoxCore | Mapping[str, JsonValue] | None, raw_roi)
    )
    if roi is None:
        return False

    return roi.x >= 0 and roi.y >= 0 and roi.width > 0 and roi.height > 0
