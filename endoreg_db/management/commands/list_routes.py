from django.core.management.base import BaseCommand
from django.urls import get_resolver, URLPattern, URLResolver


def iter_patterns(prefix, patterns):
    for p in patterns:
        if isinstance(p, URLPattern):
            yield p
        elif isinstance(p, URLResolver):
            yield from iter_patterns(prefix, p.url_patterns)


class Command(BaseCommand):
    help = "List all URL names (useful to fill policy.py)"

    def handle(self, *args, **options):
        resolver = get_resolver()
        for p in iter_patterns("", resolver.url_patterns):
            if p.name:
                self.stdout.write(p.name)
