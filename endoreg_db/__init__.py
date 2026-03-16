import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
_lx_data_models_root = _repo_root / "lx-data-models"

if _lx_data_models_root.exists():
    lx_data_models_root_str = str(_lx_data_models_root)
    if lx_data_models_root_str not in sys.path:
        sys.path.insert(0, lx_data_models_root_str)
