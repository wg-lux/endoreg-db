"""Project optional lx-dtypes employee packages into center recognition lists."""

from django.db import transaction
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.names.first_name import FirstName
from endoreg_db.models.administration.person.names.last_name import LastName


@transaction.atomic
def import_center_employees(knowledge_base: KnowledgeBase) -> int:
    """Add names atomically, preserving existing local lists and examiner identities."""
    records = list(knowledge_base.center_employee_list.values())
    examiners = [examiner for record in records for examiner in record.examiners]
    center_keys = sorted({examiner.center for examiner in examiners})
    centers = {
        str(center.center_key): center
        for center in Center.objects.select_for_update()
        .filter(center_key__in=center_keys)
        .order_by("center_key")
    }
    if len(centers) != len(center_keys):
        raise ValueError("Employee package references an unknown center_key")
    for examiner in examiners:
        center = centers[examiner.center]
        first_name, _ = FirstName.objects.get_or_create(name=examiner.first_name)
        last_name, _ = LastName.objects.get_or_create(name=examiner.last_name)
        center.first_names.add(first_name)
        center.last_names.add(last_name)
    return len(records)


def center_employee_overrides(center: Center) -> dict[str, list[str]]:
    """Omit absent lists so lx-anonymizer owns its default configuration."""
    overrides: dict[str, list[str]] = {}
    first_names = [str(row.name) for row in center.first_names.order_by("name")]
    last_names = [str(row.name) for row in center.last_names.order_by("name")]
    if first_names:
        overrides["employee_first_names"] = first_names
    if last_names:
        overrides["employee_last_names"] = last_names
    return overrides
