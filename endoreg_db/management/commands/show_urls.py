import csv
import io
import json

from django_extensions.management.commands import show_urls as django_show_urls
from lx_dtypes.models.contracts.management_command import (
    ShowUrlsCommandOptionsPayload,
    ShowUrlsRoutesPayload,
)

django_show_urls.FMTR.setdefault(
    "csv",
    "{url},{module},{url_name},{decorator}",
)


class Command(django_show_urls.Command):
    help = (
        "Displays all URL routes. Adds the csv format alias expected by the "
        "project URL contract tests."
    )

    def handle(self, *args: object, **options: object) -> str:
        options_payload = ShowUrlsCommandOptionsPayload.model_validate(options)
        if options_payload.format_style != "csv":
            return super().handle(*args, **options)

        json_options: dict[str, object] = dict(options)
        json_options["format_style"] = "json"
        raw_rows: object = json.loads(super().handle(*args, **json_options))
        rows = ShowUrlsRoutesPayload.model_validate({"routes": raw_rows}).routes
        output = io.StringIO()
        writer = csv.writer(output)
        for row in sorted(rows, key=lambda item: item.url):
            url = row.url
            if not url.startswith("/"):
                url = f"/{url}"
            writer.writerow(
                [
                    url,
                    row.module,
                    row.name,
                    row.decorators,
                ]
            )
        return output.getvalue()
