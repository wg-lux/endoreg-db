from __future__ import annotations

from datetime import date
import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_FHIR_ID_PATTERN = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")


class FhirR4Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class FhirIdentifier(FhirR4Model):
    system: str = Field(min_length=1)
    value: str = Field(min_length=1)


class FhirCoding(FhirR4Model):
    system: str = Field(min_length=1)
    code: str = Field(min_length=1)
    display: str | None = None


class FhirMeta(FhirR4Model):
    profile: list[str] = Field(min_length=1)
    tag: list[FhirCoding] = Field(min_length=1)


class FhirCodeableConcept(FhirR4Model):
    coding: list[FhirCoding] = Field(default_factory=lambda: list[FhirCoding]())
    text: str | None = None

    @model_validator(mode="after")
    def require_coding_or_text(self) -> Self:
        if not self.coding and self.text is None:
            raise ValueError("CodeableConcept requires coding or text")
        return self


class FhirReference(FhirR4Model):
    reference: str = Field(min_length=1)
    display: str | None = None


class FhirHumanName(FhirR4Model):
    use: Literal[
        "usual", "official", "temp", "nickname", "anonymous", "old", "maiden"
    ] = "official"
    family: str | None = None
    given: list[str] = Field(default_factory=lambda: list[str]())


class FhirPeriod(FhirR4Model):
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("period end must not precede start")
        return self


class FhirResource(FhirR4Model):
    id: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _FHIR_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("FHIR id contains invalid characters or exceeds 64 chars")
        return value


class FhirPatient(FhirResource):
    resource_type: Literal["Patient"] = Field(default="Patient", alias="resourceType")
    identifier: list[FhirIdentifier] = Field(min_length=1)
    name: list[FhirHumanName] | None = None
    birth_date: date | None = Field(default=None, alias="birthDate")
    gender: Literal["male", "female", "other", "unknown"] | None = None


class FhirProcedure(FhirResource):
    resource_type: Literal["Procedure"] = Field(
        default="Procedure", alias="resourceType"
    )
    status: Literal[
        "preparation",
        "in-progress",
        "not-done",
        "on-hold",
        "stopped",
        "completed",
        "entered-in-error",
        "unknown",
    ]
    code: FhirCodeableConcept
    subject: FhirReference
    performed_period: FhirPeriod | None = Field(default=None, alias="performedPeriod")


class FhirObservationComponent(FhirR4Model):
    code: FhirCodeableConcept
    value_codeable_concept: FhirCodeableConcept = Field(alias="valueCodeableConcept")


class FhirObservation(FhirResource):
    resource_type: Literal["Observation"] = Field(
        default="Observation", alias="resourceType"
    )
    status: Literal[
        "registered",
        "preliminary",
        "final",
        "amended",
        "corrected",
        "cancelled",
        "entered-in-error",
        "unknown",
    ]
    code: FhirCodeableConcept
    subject: FhirReference
    part_of: list[FhirReference] = Field(
        default_factory=lambda: list[FhirReference](), alias="partOf"
    )
    effective_date_time: date | None = Field(default=None, alias="effectiveDateTime")
    component: list[FhirObservationComponent] = Field(
        default_factory=lambda: list[FhirObservationComponent]()
    )


class FhirDiagnosticReport(FhirResource):
    resource_type: Literal["DiagnosticReport"] = Field(
        default="DiagnosticReport", alias="resourceType"
    )
    status: Literal[
        "registered",
        "partial",
        "preliminary",
        "final",
        "amended",
        "corrected",
        "appended",
        "cancelled",
        "entered-in-error",
        "unknown",
    ]
    code: FhirCodeableConcept
    subject: FhirReference
    result: list[FhirReference] = Field(default_factory=lambda: list[FhirReference]())
    imaging_study: list[FhirReference] = Field(
        default_factory=lambda: list[FhirReference](), alias="imagingStudy"
    )
    conclusion: str | None = None


class FhirImagingStudySeries(FhirR4Model):
    uid: str = Field(min_length=1, max_length=64)
    number: int | None = Field(default=None, ge=1)
    modality: FhirCoding
    number_of_instances: int = Field(ge=1, alias="numberOfInstances")


class FhirImagingStudy(FhirResource):
    resource_type: Literal["ImagingStudy"] = Field(
        default="ImagingStudy", alias="resourceType"
    )
    identifier: list[FhirIdentifier] = Field(min_length=1)
    status: Literal[
        "registered", "available", "cancelled", "entered-in-error", "unknown"
    ]
    subject: FhirReference
    started: date | None = None
    number_of_series: int = Field(ge=1, alias="numberOfSeries")
    number_of_instances: int = Field(ge=1, alias="numberOfInstances")
    series: list[FhirImagingStudySeries] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.number_of_series != len(self.series):
            raise ValueError("numberOfSeries must match series length")
        expected_instances = sum(item.number_of_instances for item in self.series)
        if self.number_of_instances != expected_instances:
            raise ValueError("numberOfInstances must match series instance counts")
        return self


FhirBundleResource = Annotated[
    FhirPatient
    | FhirProcedure
    | FhirObservation
    | FhirDiagnosticReport
    | FhirImagingStudy,
    Field(discriminator="resource_type"),
]


class FhirBundleEntry(FhirR4Model):
    full_url: str = Field(alias="fullUrl", min_length=1)
    resource: FhirBundleResource


class FhirBundle(FhirR4Model):
    resource_type: Literal["Bundle"] = Field(default="Bundle", alias="resourceType")
    id: str
    meta: FhirMeta
    identifier: FhirIdentifier
    type: Literal["collection"] = "collection"
    entry: list[FhirBundleEntry] = Field(min_length=2)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _FHIR_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("FHIR id contains invalid characters or exceeds 64 chars")
        return value

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        urls = [item.full_url for item in self.entry]
        if len(urls) != len(set(urls)):
            raise ValueError("Bundle fullUrl values must be unique")
        for item in self.entry:
            expected_url = f"{item.resource.resource_type}/{item.resource.id}"
            if item.full_url != expected_url:
                raise ValueError(
                    "Bundle fullUrl must match the contained resource identity: "
                    f"expected {expected_url}"
                )
        resources = {
            (item.resource.resource_type, item.resource.id) for item in self.entry
        }
        if len(resources) != len(self.entry):
            raise ValueError("Bundle resource identities must be unique")
        resources_by_reference = {item.full_url: item.resource for item in self.entry}

        def require_reference(reference: FhirReference, expected_type: str) -> None:
            target = resources_by_reference.get(reference.reference)
            if target is None:
                raise ValueError(
                    "Bundle contains references without matching entries: "
                    f"{reference.reference}"
                )
            if target.resource_type != expected_type:
                raise ValueError(
                    f"Bundle reference {reference.reference} must target "
                    f"{expected_type}, not {target.resource_type}"
                )

        for item in self.entry:
            resource = item.resource
            if isinstance(resource, FhirProcedure):
                require_reference(resource.subject, "Patient")
            elif isinstance(resource, FhirObservation):
                require_reference(resource.subject, "Patient")
                for reference in resource.part_of:
                    require_reference(reference, "Procedure")
            elif isinstance(resource, FhirDiagnosticReport):
                require_reference(resource.subject, "Patient")
                for reference in resource.result:
                    require_reference(reference, "Observation")
                for reference in resource.imaging_study:
                    require_reference(reference, "ImagingStudy")
            elif isinstance(resource, FhirImagingStudy):
                require_reference(resource.subject, "Patient")
        return self


def dump_fhir_r4_bundle(bundle: FhirBundle) -> dict[str, object]:
    return bundle.model_dump(mode="json", by_alias=True, exclude_none=True)


__all__ = [
    "FhirBundle",
    "FhirBundleEntry",
    "FhirCodeableConcept",
    "FhirCoding",
    "FhirDiagnosticReport",
    "FhirHumanName",
    "FhirIdentifier",
    "FhirImagingStudy",
    "FhirImagingStudySeries",
    "FhirMeta",
    "FhirObservation",
    "FhirObservationComponent",
    "FhirPatient",
    "FhirPeriod",
    "FhirProcedure",
    "FhirReference",
    "dump_fhir_r4_bundle",
]
