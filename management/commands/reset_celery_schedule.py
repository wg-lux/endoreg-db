from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask

class Command(BaseCommand):
    help = 'Deletes all periodic tasks from the database to reset the schedule'

    def handle(self, *args, **kwargs):
        PeriodicTask.objects.all().delete()
        self.stdout.write(self.style.SUCCESS('Successfully deleted all periodic tasks.'))
