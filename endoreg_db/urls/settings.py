from django.urls import path

from endoreg_db.views import (
    application_settings_detail,
    application_settings_centers_dropdown,
    application_settings_processors_dropdown,
    application_settings_annotators_dropdown,
    application_settings_report_templates_dropdown,
)


urlpatterns = [
    path(
        "settings/application/",
        application_settings_detail,
        name="application_settings_detail",
    ),
    path(
        "settings/application/dropdowns/centers/",
        application_settings_centers_dropdown,
        name="application_settings_centers_dropdown",
    ),
    path(
        "settings/application/dropdowns/processors/",
        application_settings_processors_dropdown,
        name="application_settings_processors_dropdown",
    ),
    path(
        "settings/application/dropdowns/annotators/",
        application_settings_annotators_dropdown,
        name="application_settings_annotators_dropdown",
    ),
    path(
        "settings/application/dropdowns/report_templates/",
        application_settings_report_templates_dropdown,
        name="application_settings_report_templates_dropdown",
    ),
]
