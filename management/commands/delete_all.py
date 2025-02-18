from django.core.management.base import BaseCommand
from django.apps import apps
from django.db import transaction

class Command(BaseCommand):
    help = 'Deletes all objects from all models in the "endoreg_db" app.'

    def handle(self, *args, **options):
        app_models = apps.get_models()
        app_models = [model for model in app_models if "endoreg_db" in model._meta.app_label]  # Filter models by the "endoreg_db" app
        for model in app_models:
            with transaction.atomic():  # Use a separate transaction for each model
                try:
                    model.objects.all().delete()
                    self.stdout.write(self.style.SUCCESS(f'Successfully deleted all objects in {model._meta.db_table}'))
                except Exception as e:
                    # This catches exceptions and continues with the next model
                    self.stdout.write(self.style.ERROR(f'Error deleting objects in {model._meta.db_table}: {e}'))
