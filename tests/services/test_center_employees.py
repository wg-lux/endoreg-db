from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase

from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.names.first_name import FirstName
from endoreg_db.models.administration.person.names.last_name import LastName
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.services.centers.employees import (
    center_employee_overrides,
    import_center_employees,
)

pytestmark = pytest.mark.django_db


def employee_package(*centers: str) -> KnowledgeBase:
    return KnowledgeBase.model_validate(
        {
            "config": {"name": "employees", "version": "1.0.0"},
            "center_employee_list": {
                "staff": {
                    "name": "staff",
                    "examiners": [
                        {
                            "center": center,
                            "first_name": "Test",
                            "last_name": "Employee",
                        }
                        for center in centers
                    ],
                }
            },
        }
    )


def test_import_is_additive_idempotent_and_center_scoped() -> None:
    center = Center.objects.create(name="clinic", center_key="clinic")
    other = Center.objects.create(name="other", center_key="other")
    original = FirstName.objects.create(name="Existing")
    center.first_names.add(original)
    package = employee_package("clinic")
    assert import_center_employees(package) == 1
    assert import_center_employees(package) == 1
    assert center_employee_overrides(center) == {
        "employee_first_names": ["Existing", "Test"],
        "employee_last_names": ["Employee"],
    }
    assert center_employee_overrides(other) == {}
    assert FirstName.objects.count() == 2
    assert LastName.objects.count() == 1


def test_unknown_center_rolls_back_whole_package() -> None:
    Center.objects.create(name="clinic", center_key="clinic")
    with pytest.raises(ValueError, match="unknown center_key"):
        import_center_employees(employee_package("clinic", "missing"))
    assert not FirstName.objects.exists()
    assert not LastName.objects.exists()


@pytest.mark.parametrize("with_employees", [False, True])
def test_active_report_reader_receives_only_optional_overrides(
    with_employees: bool,
) -> None:
    center = Center.objects.create(name="clinic", center_key="clinic")
    if with_employees:
        import_center_employees(employee_package("clinic"))
    report = RawPdfFile(center=center)
    reader_class = Mock()
    with patch(
        "lx_anonymizer.report_reader.ReportReader",
        reader_class,
    ):
        anonymizer = ReportAnonymizer()
        anonymizer._instantiate_report_reader(report)  # pyright: ignore[reportPrivateUsage]
    kwargs = reader_class.call_args.kwargs
    assert callable(kwargs["patient_pseudonym_resolver"])
    if with_employees:
        assert kwargs["employee_first_names"] == ["Test"]
        assert kwargs["employee_last_names"] == ["Employee"]
    else:
        assert kwargs["employee_first_names"] is None
        assert kwargs["employee_last_names"] is None


def test_command_uses_exact_package_identity() -> None:
    Center.objects.create(name="clinic", center_key="clinic")
    with patch(
        "endoreg_db.management.commands.import_center_employees.get_terminology_service",
        return_value=Mock(load=Mock(return_value=employee_package("clinic"))),
    ) as loader:
        call_command(
            "import_center_employees",
            module="employees",
            module_version="1.0.0",
            stdout=StringIO(),
        )
    loader.return_value.load.assert_called_once_with("employees", "1.0.0")
