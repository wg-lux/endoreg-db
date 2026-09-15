from typing import Literal, TypeAlias

GenderResult: TypeAlias = Literal[
    "unknown",
    "andy",
    "male",
    "female",
    "mostly_male",
    "mostly_female",
]

class NoCountryError(Exception): ...

class Detector:
    COUNTRIES: list[str]
    case_sensitive: bool

    def __init__(self, case_sensitive: bool = ...) -> None: ...
    def get_gender(
        self,
        name: str,
        country: str | None = ...,
    ) -> GenderResult: ...
