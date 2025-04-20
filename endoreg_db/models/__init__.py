from importlib import import_module

__all__: list[str] = []

for _pkg in (
    "administration",
    "label",
    "media",
    "medical",
    "other",
    "requirement",
    "rule",
    "state",
):
    module = import_module(f"{__name__}.{_pkg}")
    globals().update({k: v for k, v in module.__dict__.items() if not k.startswith("_")})
    __all__.extend(module.__all__)