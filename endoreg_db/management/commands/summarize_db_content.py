from __future__ import annotations

import csv
import datetime
from collections.abc import Iterable, Iterator, Sequence
from io import BytesIO, StringIO
from pathlib import Path
from typing import Protocol, cast

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Max, Min, fields
from django.utils.timezone import is_aware, make_naive

from endoreg_db.utils.file_operations import atomic_write_file, ensure_directory

type SummaryCell = str | int
type CsvSummaryRow = tuple[str, str]
type SummaryValue = object

EXCEL_HEADERS: CsvSummaryRow = ("Model Name", "Total Records")
DATE_FIELD_NAMES = frozenset(
    {
        "created_at",
        "updated_at",
        "timestamp",
        "date",
        "start_time",
        "end_time",
        "examination_date",
        "birth_date",
        "record_date",
    }
)
MAX_CATEGORICAL_FIELDS_TO_ANALYZE = 3
MAX_DISTINCT_VALUES_TO_SHOW = 5


class DbSummaryWorksheet(Protocol):
    title: str

    def append(self, iterable: Sequence[SummaryCell]) -> None: ...


class DbSummaryWorkbook(Protocol):
    active: DbSummaryWorksheet

    def save(self, filename: BytesIO) -> None: ...


class SummaryValuesQuerySet(Protocol):
    def annotate(self, **kwargs: object) -> SummaryValuesQuerySet: ...

    def order_by(self, *field_names: str) -> SummaryValuesQuerySet: ...

    def count(self) -> int: ...

    def __iter__(self) -> Iterator[dict[str, SummaryValue]]: ...


class SummaryModelManager(Protocol):
    def count(self) -> int: ...

    def aggregate(self, **kwargs: object) -> dict[str, SummaryValue]: ...

    def values(self, *field_names: str) -> SummaryValuesQuerySet: ...


class SummaryModelOptions(Protocol):
    def get_fields(self) -> Sequence[object]: ...


class SummaryModel(Protocol):
    objects: SummaryModelManager


class VerboseNamedField(Protocol):
    verbose_name: object


def _field_verbose_name(field: VerboseNamedField) -> str:
    return str(field.verbose_name).capitalize()


def _model_fields(model: type[SummaryModel]) -> Sequence[object]:
    model_options = cast(SummaryModelOptions, getattr(model, "_meta"))
    return model_options.get_fields()


class Command(BaseCommand):
    help = (
        "Generates a structured report summarizing the database content of custom "
        "endoreg_db models and saves it to Excel and CSV files, excluding models "
        "with zero records from the files."
    )

    def handle(self, *args: object, **options: object) -> None:
        self.stdout.write(
            self.style.SUCCESS(
                "Starting database content summarization for endoreg_db models..."
            )
        )
        workbook = _create_workbook()
        try:
            app_config = apps.get_app_config("endoreg_db")
        except LookupError:
            self.stdout.write(
                self.style.ERROR(
                    "Could not find the 'endoreg_db' app. Make sure it's correctly "
                    "installed and configured."
                )
            )
            return

        data_dir = Path(app_config.path) / "data"
        if not data_dir.exists():
            ensure_directory(data_dir)
            self.stdout.write(self.style.SUCCESS(f"Created directory: {data_dir}"))

        worksheet = workbook.active
        worksheet.title = "DB Summary"
        worksheet.append(EXCEL_HEADERS)
        csv_rows = [EXCEL_HEADERS]
        app_models = tuple(
            cast(type[SummaryModel], model) for model in app_config.get_models()
        )
        _summarize_models(self, app_models, worksheet, csv_rows)
        _write_summary_files(self, data_dir, workbook, csv_rows)
        self.stdout.write(
            self.style.SUCCESS("\nDatabase content summarization finished.")
        )


def _create_workbook() -> DbSummaryWorkbook:
    try:
        from openpyxl import Workbook  # type: ignore[import-untyped]
    except ImportError as exc:
        raise CommandError(
            "openpyxl is required to export the database summary workbook."
        ) from exc
    return cast(DbSummaryWorkbook, Workbook())


def _summarize_models(
    command: Command,
    app_models: Sequence[type[SummaryModel]],
    worksheet: DbSummaryWorksheet,
    csv_rows: list[CsvSummaryRow],
) -> None:
    if not app_models:
        command.stdout.write(
            command.style.WARNING("No models found in the 'endoreg_db' app.")
        )
        return

    for model in app_models:
        _summarize_model_safely(command, model, worksheet, csv_rows)


def _summarize_model_safely(
    command: Command,
    model: type[SummaryModel],
    worksheet: DbSummaryWorksheet,
    csv_rows: list[CsvSummaryRow],
) -> None:
    model_name = model.__name__
    command.stdout.write(command.style.HTTP_INFO(f"\n--- Model: {model_name} ---"))
    try:
        _summarize_model(command, model, worksheet, csv_rows)
    except Exception as exc:
        command.stdout.write(
            command.style.ERROR(f"  Error processing model {model_name}: {exc}")
        )


def _summarize_model(
    command: Command,
    model: type[SummaryModel],
    worksheet: DbSummaryWorksheet,
    csv_rows: list[CsvSummaryRow],
) -> None:
    count = model.objects.count()
    command.stdout.write(f"  Total records: {count}")
    if count == 0:
        command.stdout.write(
            command.style.NOTICE(
                "  No records found for this model. Skipping from file output."
            )
        )
        return

    worksheet.append([model.__name__, count])
    csv_rows.append((model.__name__, str(count)))
    _write_first_date_range(command, model)
    _write_categorical_summaries(command, model)


def _relevant_date_fields(
    model: type[SummaryModel],
) -> Iterable[fields.Field[object, object]]:
    for field in _model_fields(model):
        if isinstance(field, (fields.DateField, fields.DateTimeField)):
            typed_field = cast(fields.Field[object, object], field)
            if typed_field.name in DATE_FIELD_NAMES:
                yield typed_field


def _date_range(
    model: type[SummaryModel],
    field: fields.Field[object, object],
) -> tuple[SummaryValue, SummaryValue] | None:
    aggregation = model.objects.aggregate(
        min_date=Min(field.name),
        max_date=Max(field.name),
    )
    min_value = aggregation.get("min_date")
    max_value = aggregation.get("max_date")
    if min_value is None or max_value is None:
        return None
    return min_value, max_value


def _format_date_value(value: SummaryValue) -> SummaryValue:
    if isinstance(value, datetime.datetime) and is_aware(value):
        return make_naive(value).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return value


def _write_first_date_range(
    command: Command,
    model: type[SummaryModel],
) -> None:
    for field in _relevant_date_fields(model):
        try:
            values = _date_range(model, field)
        except Exception as exc:
            command.stdout.write(
                command.style.WARNING(
                    f"  Could not aggregate date range for '{field.name}': {exc}"
                )
            )
            continue
        if values is None:
            continue
        min_value, max_value = values
        command.stdout.write(
            f"  {_field_verbose_name(cast(VerboseNamedField, field))} range: "
            f"{_format_date_value(min_value)} to {_format_date_value(max_value)}"
        )
        return


def _is_related_categorical(field: fields.Field[object, object]) -> bool:
    return bool(field.related_model) and bool(field.many_to_one or field.one_to_one)


def _is_potential_categorical(field: fields.Field[object, object]) -> bool:
    if _is_related_categorical(field):
        return True
    if isinstance(field, fields.BooleanField):
        return True
    if not isinstance(
        field,
        (fields.CharField, fields.IntegerField, fields.TextField),
    ):
        return False
    field_name = field.name.lower()
    return any(
        token in field_name for token in ("type", "status", "gender", "category")
    )


def _categorical_fields(
    model: type[SummaryModel],
) -> Iterable[fields.Field[object, object]]:
    for field in _model_fields(model):
        if not isinstance(field, fields.Field):
            continue
        typed_field = cast(fields.Field[object, object], field)
        if _is_potential_categorical(typed_field):
            yield typed_field


def _display_value(value: SummaryValue) -> str:
    display = str(value) if value is not None else "None/NULL"
    if isinstance(value, str) and len(display) > 50:
        return display[:47] + "..."
    return display


def _write_value_counts(
    command: Command,
    model: type[SummaryModel],
    field: fields.Field[object, object],
) -> None:
    command.stdout.write(
        f"  Value counts for '{_field_verbose_name(cast(VerboseNamedField, field))}':"
    )
    values = (
        model.objects.values(field.name)
        .annotate(count=Count(field.name))
        .order_by("-count")
    )
    distinct_count = values.count()
    if distinct_count == 0:
        command.stdout.write("    No distinct values found or field is often NULL.")
        return

    for index, item in enumerate(values):
        if index >= MAX_DISTINCT_VALUES_TO_SHOW:
            command.stdout.write(
                f"    ... and {distinct_count - MAX_DISTINCT_VALUES_TO_SHOW} "
                "more distinct values."
            )
            return
        command.stdout.write(
            f"    - {_display_value(item[field.name])}: {item['count']}"
        )


def _write_categorical_summaries(
    command: Command,
    model: type[SummaryModel],
) -> None:
    fields_to_analyze = list(_categorical_fields(model))[
        :MAX_CATEGORICAL_FIELDS_TO_ANALYZE
    ]
    for field in fields_to_analyze:
        try:
            _write_value_counts(command, model, field)
        except Exception as exc:
            command.stdout.write(
                command.style.WARNING(
                    f"  Could not get value counts for '{field.name}': {exc}"
                )
            )


def _write_summary_files(
    command: Command,
    data_dir: Path,
    workbook: DbSummaryWorkbook,
    csv_rows: Sequence[CsvSummaryRow],
) -> None:
    _write_workbook_safely(command, data_dir / "db_summary.xlsx", workbook)
    _write_csv_safely(command, data_dir / "db_summary.csv", csv_rows)


def _write_workbook_safely(
    command: Command,
    destination: Path,
    workbook: DbSummaryWorkbook,
) -> None:
    try:
        buffer = BytesIO()
        workbook.save(buffer)
        atomic_write_file(destination=destination, content=[buffer.getvalue()])
        command.stdout.write(
            command.style.SUCCESS(f"\nDatabase summary report saved to {destination}")
        )
    except Exception as exc:
        command.stdout.write(command.style.ERROR(f"\nError saving Excel file: {exc}"))


def _write_csv_safely(
    command: Command,
    destination: Path,
    rows: Sequence[CsvSummaryRow],
) -> None:
    try:
        buffer = StringIO(newline="")
        csv.writer(buffer).writerows(rows)
        atomic_write_file(
            destination=destination,
            content=[buffer.getvalue().encode("utf-8")],
        )
        command.stdout.write(
            command.style.SUCCESS(f"Database summary report saved to {destination}")
        )
    except Exception as exc:
        command.stdout.write(command.style.ERROR(f"Error saving CSV file: {exc}"))
