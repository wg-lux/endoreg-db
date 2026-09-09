from django.test import SimpleTestCase
from django.urls import get_resolver


class testDeploymentConfiguration(SimpleTestCase):
    def test_eager_import_and_url_resolution(self):
        """
        Forces Django to resolve all URL patterns and load all referenced
        views, settings, permissions, and serializers, catching any
        hidden circular imports or configuration type issues.
        """
        try:
            resolver = get_resolver()
            # This forces Django to evaluate the entirety of the URL configuration tree
            resolver.url_patterns
        except Exception as e:
            self.fail(
                f"Django bootstrap failed due to an import/configuration error: {e}"
            )
