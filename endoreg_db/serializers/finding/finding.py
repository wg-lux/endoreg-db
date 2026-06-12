from __future__ import annotations

from typing import Protocol, cast

from rest_framework import serializers
from ..finding_classification import (
    FindingClassificationSerializer,
    # FindingClassificationChoiceSerializer,
)
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
)
from endoreg_db.models.medical.finding.finding import Finding


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> list[dict[str, object]]: ...


def _serializer_data(serializer: object) -> list[dict[str, object]]:
    return cast(_SerializerDataLike, serializer).data


class _FindingLike(Protocol):
    finding_classifications: _FindingClassificationRelation


class _FindingClassificationRelation(Protocol):
    def filter(
        self, *args: object, **kwargs: object
    ) -> list[FindingClassification]: ...

    def all(self) -> list[FindingClassification]: ...


class FindingSerializer(serializers.ModelSerializer[Finding]):
    location_classifications = serializers.SerializerMethodField()
    morphology_classifications = serializers.SerializerMethodField()
    classifications = serializers.SerializerMethodField()

    class Meta:  # type: ignore[reportIncompatibleVariableOverride]
        model = Finding
        fields = [
            "id",
            "name",
            "description",
            "classifications",
            "location_classifications",
            "morphology_classifications",
        ]

    def get_location_classifications(
        self, obj: _FindingLike
    ) -> list[dict[str, object]]:
        """
        Retrieve and serialize all 'location' classifications associated with the given Finding instance.

        Returns:
            list: Serialized data for each related classification of type 'location'.
        """
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="location"
        )
        return _serializer_data(
            FindingClassificationSerializer(classifications, many=True)
        )

    def get_morphology_classifications(
        self, obj: _FindingLike
    ) -> list[dict[str, object]]:
        """
        Return serialized morphology classifications associated with the given finding.

        Parameters:
            obj: The Finding instance whose morphology classifications are retrieved.

        Returns:
            list: Serialized data for all related morphology classifications.
        """
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="morphology"
        )
        return _serializer_data(
            FindingClassificationSerializer(classifications, many=True)
        )

    def get_classifications(self, obj: _FindingLike) -> list[dict[str, object]]:
        """
        Retrieve all classifications related to the given finding.

        Returns:
            list: Serialized representations of all classifications associated with the finding.
        """
        return _serializer_data(
            FindingClassificationSerializer(
                obj.finding_classifications.all(), many=True
            )
        )
