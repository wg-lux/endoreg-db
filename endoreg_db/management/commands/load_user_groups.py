
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission


class Command(BaseCommand):
    help = """Create additional user groups"""

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Display verbose output',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']

        permissions = [
            "g_play_production",
            "agl",
            "endo_reg_production",
            "ukw_production"
        ]

        for permission_name in permissions:
            permission, created = Permission.objects.get_or_create(codename=permission_name)
            if verbose:
                if created:
                    self.stdout.write(self.style.SUCCESS("Created Permission {}".format(group_name)))

        groups = ["demo", "verified", "agl", "endo_reg_user", "g_play_user", "ukw_user"]
        for group_name in groups:
            _group, created = Group.objects.get_or_create(name=group_name)
            if verbose:
                if created:
                    self.stdout.write(self.style.SUCCESS("Created group {}".format(group_name)))

        # Add permissions to groups
        # demo has no permissions
        # verified has no permissions
        # agl has all permissions
        # endo_reg_user has endo_reg_user_production permission
        # g_play_user has g_play_production permission
        agl_group = Group.objects.get(name="agl")
        agl_group.permissions.set(Permission.objects.all())
        agl_group.save()

        endo_reg_user_group = Group.objects.get(name="endo_reg_user")
        _permission = Permission.objects.get(codename="endo_reg_production")
        endo_reg_user_group.permissions.add(_permission)
        endo_reg_user_group.save()

        g_play_user_group = Group.objects.get(name="g_play_user")
        _permission = Permission.objects.get(codename="g_play_production")
        g_play_user_group.permissions.add(_permission)
        g_play_user_group.save()

        ukw_user_group = Group.objects.get(name="ukw_user")
        _permission = Permission.objects.get(codename="ukw_production")
        ukw_user_group.permissions.add(_permission)
        ukw_user_group.save()
        


            
        