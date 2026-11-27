from __future__ import annotations

from typing import Protocol, cast, Any, Unpack

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import ValidationError as SchemaError
from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from lx_dtypes.models.contracts.application_settings import (
    ANNOTATION_PROFESSION_LABELS_DE,
    validated_annotation_academic_titles,
    AnnotationProfession,
)


class _PortalUserSource(Protocol):
    username: str


class PortalUserInfo(models.Model):
    user: models.OneToOneField[Any, Any] = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE
    )
    profession: models.ForeignKey[Any, Any] = models.ForeignKey(
        "endoreg_db.Profession",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_infos",
    )
    annotation_profession: models.CharField[str | None, str | None] = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        choices=list(ANNOTATION_PROFESSION_LABELS_DE.items()),
    )
    annotation_academic_titles: models.JSONField[list[str], list[str]] = (
        models.JSONField(default=list, blank=True)
    )
    works_in_endoscopy: models.BooleanField[Any, Any] = models.BooleanField(
        blank=True, null=True
    )
    # Add other fields as needed

    examiner: models.OneToOneField[Any, Any] = models.OneToOneField(
        "endoreg_db.Examiner",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_info",
    )
    centers: models.ManyToManyField[Any, Any] = models.ManyToManyField(
        "endoreg_db.Center",
        blank=True,
        related_name="authorized_portal_user_infos",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    annotation_profession__in=[
                        role.value for role in AnnotationProfession
                    ]
                )
                | models.Q(annotation_profession__isnull=True),
                name="annotator_profile_valid_profession",
            )
        ]

    def clean(self) -> None:
        super().clean()
        try:
            titles = validated_annotation_academic_titles(
                self.annotation_academic_titles
            )
        except SchemaError as exc:
            raise ValidationError(
                {"annotation_academic_titles": "Invalid academic titles."}
            ) from exc
        self.annotation_academic_titles = titles

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        user = cast(_PortalUserSource, self.user)
        return user.username
