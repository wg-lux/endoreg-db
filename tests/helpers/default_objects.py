from endoreg_db.models import Center
DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
DEFAULT_ENDOSCOPE_NAME = "test_endoscope"


def default_center():
    """
    Create a default Center instance for testing.
    """
    center = Center.objects.create(
        name=DEFAULT_CENTER_NAME,
        name_de="Universitätsklinikum Würzburg",
        name_en="University Hospital Würzburg",
    )
    return center