from django.core.management.base import BaseCommand
from django.apps import apps
from django.db.models import Min, Max, Count, fields
from django.utils.timezone import is_aware, make_naive
import datetime
import os
from openpyxl import Workbook
from django.conf import settings
import csv

class Command(BaseCommand):
    help = 'Generates a structured report summarizing the database content of custom endoreg_db models and saves it to Excel and CSV files, excluding models with zero records from the files.' # Updated help text

    def handle(self, *args, **options):
        """
        Generates a summary report of all models in the 'endoreg_db' app and exports the results to Excel and CSV files.
        
        For each model, counts the total number of records and includes only models with at least one record in the output files. For models with records, attempts to display the range of values for common date or datetime fields and provides value counts for up to three categorical or ForeignKey fields, using heuristics based on field type and name. Handles missing app configuration, file saving errors, and aggregation exceptions gracefully, outputting progress and warnings to the console. Creates a 'data' directory within the app if it does not exist and saves the reports there.
        """
        self.stdout.write(self.style.SUCCESS("Starting database content summarization for endoreg_db models..."))

        try:
            app_config = apps.get_app_config('endoreg_db')
        except LookupError:
            self.stdout.write(self.style.ERROR("Could not find the 'endoreg_db' app. Make sure it's correctly installed and configured."))
            return

        data_dir = os.path.join(app_config.path, 'data')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            self.stdout.write(self.style.SUCCESS(f"Created directory: {data_dir}"))
        
        excel_file_path = os.path.join(data_dir, 'db_summary.xlsx')
        csv_file_path = os.path.join(data_dir, 'db_summary.csv')

        # --- Excel Setup ---
        wb = Workbook()
        ws = wb.active
        ws.title = "DB Summary"
        excel_headers = ["Model Name", "Total Records"]
        ws.append(excel_headers)
        # --- End Excel Setup ---

        # --- CSV Setup ---
        csv_data_for_file = [excel_headers] # Initialize with headers for CSV, will only store rows with count > 0
        # --- End CSV Setup ---
            
        models = app_config.get_models()

        if not models:
            self.stdout.write(self.style.WARNING("No models found in the 'endoreg_db' app."))
            try:
                wb.save(excel_file_path) # wb will only have headers
                self.stdout.write(self.style.SUCCESS(f"Empty summary report saved to {excel_file_path}"))
                with open(csv_file_path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerows(csv_data_for_file) # csv_data_for_file will only contain headers
                self.stdout.write(self.style.SUCCESS(f"Empty summary report saved to {csv_file_path}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error saving files: {e}"))
            return

        for model in models:
            model_name = model.__name__
            self.stdout.write(self.style.HTTP_INFO(f"\n--- Model: {model_name} ---"))

            try:
                count = model.objects.count()
                self.stdout.write(f"  Total records: {count}")

                if count == 0:
                    self.stdout.write(self.style.NOTICE("  No records found for this model. Skipping from file output."))
                    continue # Skip further processing for this model if count is 0 for file output

                # --- Add to Excel (only if count > 0) ---
                ws.append([model_name, count])
                # --- End Add to Excel ---

                # --- Add to CSV data list (only if count > 0) ---
                csv_data_for_file.append([model_name, count])
                # --- End Add to CSV data list ---
                
                # Attempt to get date ranges for common date/datetime fields
                date_fields_to_check = ['created_at', 'updated_at', 'timestamp', 'date',
                                        'start_time', 'end_time', 'examination_date',
                                        'birth_date', 'record_date']
                processed_date_field = False
                for field_obj in model._meta.get_fields():
                    if field_obj.name in date_fields_to_check and isinstance(field_obj, (fields.DateField, fields.DateTimeField)):
                        try:
                            aggregation = model.objects.aggregate(min_date=Min(field_obj.name), max_date=Max(field_obj.name))
                            min_val = aggregation.get('min_date')
                            max_val = aggregation.get('max_date')

                            if min_val is not None and max_val is not None:
                                if isinstance(min_val, datetime.datetime) and is_aware(min_val):
                                    min_val = make_naive(min_val).strftime('%Y-%m-%d %H:%M:%S')
                                elif isinstance(min_val, datetime.date):
                                    min_val = min_val.strftime('%Y-%m-%d')
                                
                                if isinstance(max_val, datetime.datetime) and is_aware(max_val):
                                    max_val = make_naive(max_val).strftime('%Y-%m-%d %H:%M:%S')
                                elif isinstance(max_val, datetime.date):
                                    max_val = max_val.strftime('%Y-%m-%d')

                                self.stdout.write(f"  {field_obj.verbose_name.capitalize()} range: {min_val} to {max_val}")
                                processed_date_field = True
                                break # Process only the first found relevant date field for brevity
                        except Exception as e:
                            self.stdout.write(self.style.WARNING(f"  Could not aggregate date range for '{field_obj.name}': {e}"))
                
                # Attempt to get value counts for potential categorical/ForeignKey fields
                categorical_fields_found = 0
                MAX_CATEGORICAL_FIELDS_TO_ANALYZE = 3
                MAX_DISTINCT_VALUES_TO_SHOW = 5

                for field in model._meta.get_fields():
                    if categorical_fields_found >= MAX_CATEGORICAL_FIELDS_TO_ANALYZE:
                        break

                    is_potential_categorical = False
                    field_name_lower = field.name.lower()

                    if field.many_to_one or field.one_to_one: # ForeignKey or OneToOneField
                        if field.related_model:
                            is_potential_categorical = True
                    elif isinstance(field, (fields.CharField, fields.IntegerField, fields.BooleanField, fields.TextField)):
                        # Heuristic for common categorical fields by name or type
                        if 'type' in field_name_lower or \
                           'status' in field_name_lower or \
                           'gender' in field_name_lower or \
                           'category' in field_name_lower or \
                           isinstance(field, fields.BooleanField):
                           is_potential_categorical = True
                        # For CharField/TextField, also consider if it has few distinct values (expensive to check upfront for all)
                        # We'll rely on the naming heuristic primarily or if it's a boolean.

                    if is_potential_categorical:
                        try:
                            self.stdout.write(f"  Value counts for '{field.verbose_name.capitalize() if hasattr(field, 'verbose_name') else field.name}':")
                            
                            # QuerySet for distinct values and their counts
                            values_qs = model.objects.values(field.name).annotate(count=Count(field.name)).order_by('-count')
                            
                            distinct_values_count = values_qs.count()

                            if distinct_values_count == 0:
                                self.stdout.write(f"    No distinct values found or field is often NULL.")
                                continue

                            for i, item in enumerate(values_qs):
                                if i >= MAX_DISTINCT_VALUES_TO_SHOW:
                                    self.stdout.write(f"    ... and {distinct_values_count - MAX_DISTINCT_VALUES_TO_SHOW} more distinct values.")
                                    break
                                val = item[field.name]
                                val_display = str(val) if val is not None else "None/NULL"
                                # Truncate long string values for display
                                if isinstance(val, str) and len(val_display) > 50:
                                    val_display = val_display[:47] + "..."
                                self.stdout.write(f"    - {val_display}: {item['count']}")
                            
                            categorical_fields_found += 1

                        except Exception as e:
                            # Some fields (like generic relations or complex custom fields) might not support this directly
                            self.stdout.write(self.style.WARNING(f"  Could not get value counts for '{field.name}': {e}"))
            
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error processing model {model_name}: {e}"))

        # --- Save Excel File ---
        try:
            wb.save(excel_file_path)
            self.stdout.write(self.style.SUCCESS(f"\nDatabase summary report saved to {excel_file_path}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\nError saving Excel file: {e}"))
        # --- End Save Excel File ---

        # --- Save CSV File ---
        try:
            with open(csv_file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(csv_data_for_file)
            self.stdout.write(self.style.SUCCESS(f"Database summary report saved to {csv_file_path}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error saving CSV file: {e}"))
        # --- End Save CSV File ---

        self.stdout.write(self.style.SUCCESS("\nDatabase content summarization finished."))
