from django.core.management.base import BaseCommand
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.apps import apps

class Command(BaseCommand):
    help = 'Sets up default permissions for each model in the app.'

    def handle(self, *args, **options):
        for model in apps.get_models():
            content_type = ContentType.objects.get_for_model(model)
            permissions = ['view', 'add', 'change', 'delete']
            for perm in permissions:
                codename = f'{perm}_{model._meta.model_name}'
                name = f'Can {perm} {model._meta.verbose_name}'
                Permission.objects.get_or_create(
                    codename=codename,
                    defaults={'name': name, 'content_type': content_type},
                )
            self.stdout.write(self.style.SUCCESS(f'Set up permissions for {model._meta.verbose_name}'))
