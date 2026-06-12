# eendoreg_db/serializers/examination/base.py
from typing import Protocol, cast, TYPE_CHECKING

from django.db.models.query import QuerySet
from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object

from ...models import Examination, ExaminationType


class _ExaminationTypeManagerLike(Protocol):
    def all(self) -> QuerySet[ExaminationType, ExaminationType]: ...


class _SerializerDataLike:
    @property
    def data(self) -> list[dict[str, object]]: ...


def _serializer_data(serializer: object) -> list[dict[str, object]]:
    return cast(_SerializerDataLike, serializer).data


class ExaminationTypeSerializer(serializers.ModelSerializer[ExaminationType]):
    class Meta(_ModelSerializerMeta):
        model = ExaminationType  # pyright: ignore[reportAssignmentType]
        fields = ["id", "name"]


class ExaminationSerializer(serializers.ModelSerializer[Examination]):
    findings = serializers.SerializerMethodField()
    examination_types = serializers.SerializerMethodField()

    class Meta(_ModelSerializerMeta):
        model = Examination  # pyright: ignore[reportAssignmentType]
        fields = ["id", "name", "findings", "examination_types"]

    def get_findings(self, obj: Examination) -> list[dict[str, object]]:
        """
        Return a list of serialized findings associated with the given examination.

        Parameters:
            obj (Examination): The examination instance for which to retrieve findings.

        Returns:
            list: Serialized data for all findings available to the examination.
        """
        from ..finding import FindingSerializer

        findings = obj.get_available_findings()
        return list(_serializer_data(FindingSerializer(findings, many=True)))

    def get_examination_types(self, obj: Examination) -> list[dict[str, object]]:
        """
        Return a list of serialized examination types associated with the given examination.

        Parameters:
            obj (Examination): The examination instance for which to retrieve examination types.

        Returns:
            list: Serialized data for each related examination type.
        """
        examination_types = cast(
            _ExaminationTypeManagerLike, getattr(obj, "examination_types")
        ).all()
        return list(
            _serializer_data(
                ExaminationTypeSerializer(examination_types, many=True)
            )
        )
