from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.apps import apps


class Command(BaseCommand):
    help = "Create additional user groups and permissions for all models in 'endoreg_db' app."

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Display verbose output',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']

        # Create groups
        groups = ["demo", "verified", "agl", "endo_reg_user", "g_play_user", "ukw_user"]
        for group_name in groups:
            _group, created = Group.objects.get_or_create(name=group_name)
            if verbose and created:
                self.stdout.write(self.style.SUCCESS(f"Created group {group_name}"))

        if verbose:
            self.stdout.write(self.style.SUCCESS("All groups processed successfully."))
