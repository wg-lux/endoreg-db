from __future__ import annotations

from lx_dtypes.models.contracts.endoscopy_processor import roi_box_or_none_from_object


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
    roi = roi_box_or_none_from_object(raw_roi)
    if roi is None:
        return False

    return roi.x >= 0 and roi.y >= 0 and roi.width > 0 and roi.height > 0
