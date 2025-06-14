"""
Management command to check and configure authentication settings.
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from rest_framework.permissions import IsAuthenticated, AllowAny
import os


class Command(BaseCommand):
    help = 'Check and configure authentication settings based on environment'

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode',
            choices=['check', 'dev', 'prod'],
            default='check',
            help='Mode: check current settings, set dev mode, or set prod mode'
        )
        parser.add_argument(
            '--show-permissions',
            action='store_true',
            help='Show which permission classes will be used'
        )

    def handle(self, *args, **options):
        mode = options['mode']
        
        if mode == 'check':
            self.check_current_settings()
        elif mode == 'dev':
            self.set_dev_mode()
        elif mode == 'prod':
            self.set_prod_mode()
            
        if options['show_permissions']:
            self.show_permission_classes()

    def check_current_settings(self):
        """Check current authentication configuration."""
        debug_mode = getattr(settings, 'DEBUG', False)
        settings_module = os.environ.get('DJANGO_SETTINGS_MODULE', 'Unknown')
        
        self.stdout.write("\n" + "="*50)
        self.stdout.write("🔍 AUTHENTICATION CONFIGURATION CHECK")
        self.stdout.write("="*50)
        
        self.stdout.write(f"Settings Module: {settings_module}")
        self.stdout.write(f"DEBUG Mode: {debug_mode}")
        
        if debug_mode:
            self.stdout.write(self.style.WARNING("🔓 Authentication: DISABLED (AllowAny)"))
            self.stdout.write("   - All API endpoints are accessible without login")
            self.stdout.write("   - Suitable for development and testing")
        else:
            self.stdout.write(self.style.SUCCESS("🔒 Authentication: ENABLED (IsAuthenticated)"))
            self.stdout.write("   - API endpoints require valid authentication")
            self.stdout.write("   - Suitable for production deployment")
            
        # Check if Keycloak is configured
        keycloak_server = getattr(settings, 'KEYCLOAK_SERVER_URL', None)
        if keycloak_server:
            self.stdout.write(f"Keycloak Server: {keycloak_server}")
        else:
            self.stdout.write(self.style.WARNING("⚠️  Keycloak not configured"))
            
        self.stdout.write("="*50 + "\n")

    def set_dev_mode(self):
        """Instructions for setting development mode."""
        self.stdout.write(self.style.SUCCESS("\n🛠️  DEVELOPMENT MODE SETUP"))
        self.stdout.write("To enable development mode with disabled authentication:")
        self.stdout.write("")
        self.stdout.write("1. Use dev settings:")
        self.stdout.write("   export DJANGO_SETTINGS_MODULE=dev.dev_settings")
        self.stdout.write("   python manage.py runserver")
        self.stdout.write("")
        self.stdout.write("2. Or run with explicit settings:")
        self.stdout.write("   python manage.py runserver --settings=dev.dev_settings")
        self.stdout.write("")
        self.stdout.write("This will set DEBUG=True and disable authentication requirements.")

    def set_prod_mode(self):
        """Instructions for setting production mode."""
        self.stdout.write(self.style.SUCCESS("\n🏭 PRODUCTION MODE SETUP"))
        self.stdout.write("To enable production mode with authentication:")
        self.stdout.write("")
        self.stdout.write("1. Use production settings:")
        self.stdout.write("   export DJANGO_SETTINGS_MODULE=prod_settings")
        self.stdout.write("   python manage.py runserver --settings=prod_settings")
        self.stdout.write("")
        self.stdout.write("2. Set required environment variables:")
        self.stdout.write("   export DJANGO_SECRET_KEY='your-secure-secret-key'")
        self.stdout.write("   export KEYCLOAK_CLIENT_SECRET='your-keycloak-secret'")
        self.stdout.write("")
        self.stdout.write("This will set DEBUG=False and enable authentication requirements.")

    def show_permission_classes(self):
        """Show which permission classes are being used."""
        debug_mode = getattr(settings, 'DEBUG', False)
        
        self.stdout.write("\n" + "="*40)
        self.stdout.write("🔐 PERMISSION CLASSES IN USE")
        self.stdout.write("="*40)
        
        if debug_mode:
            permission_class = "AllowAny"
            icon = "🔓"
        else:
            permission_class = "IsAuthenticated"
            icon = "🔒"
            
        self.stdout.write(f"{icon} Current Mode: {permission_class}")
        self.stdout.write("")
        self.stdout.write("Views using dynamic permissions:")
        self.stdout.write("  • video_segments_view")
        self.stdout.write("  • VideoViewSet")
        self.stdout.write("  • Other API endpoints")
        self.stdout.write("")
        self.stdout.write("Views always requiring authentication:")
        self.stdout.write("  • video_segment_detail_view")
        self.stdout.write("  • video_segments_by_label_id_view")
        self.stdout.write("  • video_segments_by_label_name_view")
        self.stdout.write("="*40 + "\n")