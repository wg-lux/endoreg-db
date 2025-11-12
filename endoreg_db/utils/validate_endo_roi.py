from typing import Dict, Union


def validate_endo_roi(endo_roi_dict: Union[Dict[str, int], None]):
    """
    Validate endoscope ROI dictionary. The dictionary must have the following
    keys: x, y, width, height. The values must be greater than or equal to 0
    for x and y, and greater than 0 for width and height.
    """
    if not endo_roi_dict:
        return False

    for key, value in endo_roi_dict.items():
        if key == "x":
            assert value >= 0, f"Endoscope ROI x value must be greater than or equal to 0. Got {value}"
        elif key == "y":
            assert value >= 0, f"Endoscope ROI y value must be greater than or equal to 0. Got {value}"
        elif key == "width":
            assert value > 0, f"Endoscope ROI width value must be greater than 0. Got {value}"
        elif key == "height":
            assert value > 0, f"Endoscope ROI height value must be greater than 0. Got {value}"
        else:
            raise ValueError(f"Endoscope ROI key {key} not recognized")

    return True
