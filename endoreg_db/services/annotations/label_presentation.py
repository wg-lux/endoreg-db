"""Resolve optional study label presentation without changing annotation identity."""

from collections.abc import Iterable
from typing import cast

from django.contrib.auth.models import Group, User
from django.shortcuts import get_object_or_404
from lx_dtypes.models.contracts.study_label_presentation import (
    StudyAnnotationLabel,
    StudyAnnotationLabelCatalog,
    StudyLabelPresentation,
)
from lx_dtypes.models.contracts.study_metadata import StudyMetadata
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request

from endoreg_db.authz.policy import satisfies
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.other.study_cohort import StudyCohort


def validate_label_presentation(
    presentation: StudyLabelPresentation | None,
    *,
    labels: Iterable[Label] | None = None,
) -> dict[str, Label]:
    """Resolve configured natural names once, rejecting missing references."""
    if presentation is None:
        return {}
    names = {item.name for item in presentation.labels}
    resolved: dict[str, Label] = {}
    rows = (
        labels
        if labels is not None
        else Label.objects.filter(name__in=names).order_by("pk")
    )
    for label in rows:
        if str(label.name) in names:
            resolved.setdefault(str(label.name), label)
    missing = names - resolved.keys()
    if missing:
        raise ValueError("Unknown configured labels: " + ", ".join(sorted(missing)))
    return resolved


def study_label_presentation(
    request: Request,
    cohort_id: int | None,
) -> StudyLabelPresentation | None:
    if cohort_id is None:
        return None
    user = cast(User, request.user)
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("Study label settings require an authenticated user.")
    if not bool(getattr(user, "is_superuser", False)) and not satisfies(
        set(Group.objects.filter(user=user).values_list("name", flat=True)),
        "patient:read",
    ):
        raise PermissionDenied("Study label settings require patient:read permission.")
    cohort = get_object_or_404(StudyCohort, pk=cohort_id, created_by_id=user.pk)
    return StudyMetadata.model_validate(cohort.study_metadata).label_presentation


def parse_study_cohort_id(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValidationError({"study_cohort_id": "Must be a positive integer."})
    try:
        result = int(value)
    except ValueError as error:
        raise ValidationError(
            {"study_cohort_id": "Must be a positive integer."}
        ) from error
    if result < 1:
        raise ValidationError({"study_cohort_id": "Must be a positive integer."})
    return result


def require_study_labels(
    request: Request,
    cohort_id: int | None,
    *,
    label_ids: Iterable[int] = (),
    label_names: Iterable[str] = (),
    segment_ids: Iterable[int] = (),
    video_id: int | None = None,
) -> None:
    presentation = study_label_presentation(request, cohort_id)
    if presentation is None or not presentation.locked:
        return
    try:
        allowed = validate_label_presentation(presentation)
    except ValueError as error:
        raise ValidationError({"study_cohort_id": str(error)}) from error
    ids = set(label_ids)
    rows = list(Label.objects.filter(pk__in=ids).values_list("pk", "name"))
    if len(rows) != len(ids):
        raise ValidationError({"labels": "One or more labels do not exist."})
    selected_names = set(label_names) | {str(name) for _, name in rows}
    selected_names.update(
        str(name)
        for name in LabelVideoSegment.objects.filter(
            pk__in=set(segment_ids),
            video_file_id=video_id,
        ).values_list("label__name", flat=True)
    )
    if selected_names - allowed.keys():
        raise ValidationError(
            {"labels": "The selected study only permits its configured labels."}
        )


def _label_name_de(label: Label) -> str | None:
    """Catalogue German name, or None; never the identifier."""
    value = label.name_de
    return value if isinstance(value, str) and value.strip() else None


def build_label_catalog(
    request: Request, cohort_id: int | None
) -> StudyAnnotationLabelCatalog:
    presentation = study_label_presentation(request, cohort_id)
    labels = list(Label.objects.all().order_by("name", "pk"))
    configured = presentation.labels if presentation is not None else []
    try:
        by_name = validate_label_presentation(presentation, labels=labels)
    except ValueError as error:
        raise ValidationError({"labels": str(error)}) from error
    result = [
        StudyAnnotationLabel(
            id=int(by_name[item.name].pk),
            name=item.name,
            # A study display name wins; otherwise the catalogue German name.
            display_name=item.display_name or _label_name_de(by_name[item.name]),
            color=item.color,
        )
        for item in configured
    ]
    locked = presentation.locked if presentation is not None else False
    if not locked:
        included = {item.id for item in result}
        result.extend(
            StudyAnnotationLabel(
                id=int(label.pk),
                name=str(label.name),
                display_name=_label_name_de(label),
                color=None,
            )
            for label in labels
            if int(label.pk) not in included
        )
    return StudyAnnotationLabelCatalog(
        study_cohort_id=cohort_id,
        locked=locked,
        labels=result,
        scopes=presentation.scopes if presentation is not None else [],
    )
