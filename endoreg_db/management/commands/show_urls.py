import csv
import io
import json

from django_extensions.management.commands import show_urls as django_show_urls

django_show_urls.FMTR.setdefault(
    "csv",
    "{url},{module},{url_name},{decorator}",
)


class Command(django_show_urls.Command):
    help = (
        "Displays all URL routes. Adds the csv format alias expected by the "
        "project URL contract tests."
    )

    def handle(self, *args, **options):
        if options.get("format_style") != "csv":
            return super().handle(*args, **options)

        json_options = dict(options)
        json_options["format_style"] = "json"
        rows = json.loads(super().handle(*args, **json_options))
        output = io.StringIO()
        writer = csv.writer(output)
        for row in sorted(rows, key=lambda item: str(item.get("url", ""))):
            url = str(row.get("url", "") or "")
            if not url.startswith("/"):
                url = f"/{url}"
            writer.writerow(
                [
                    url,
                    row.get("module", ""),
                    row.get("name", ""),
                    row.get("decorators", ""),
                ]
            )
        return output.getvalue()
