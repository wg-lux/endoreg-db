from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import cast

from django.core.management.base import BaseCommand
from django.urls import URLPattern, URLResolver, get_resolver


type UrlPatternNode = URLPattern | URLResolver


def iter_patterns(patterns: Iterable[UrlPatternNode]) -> Iterator[URLPattern]:
    for pattern in patterns:
        if isinstance(pattern, URLPattern):
            yield pattern
        else:
            yield from iter_patterns(cast(Iterable[UrlPatternNode], pattern.url_patterns))


class Command(BaseCommand):
    help = "List all URL names (useful to fill policy.py)"

    def handle(self, *args: str, **options: str) -> None:
        resolver = get_resolver()
        url_patterns = cast(Iterable[UrlPatternNode], resolver.url_patterns)
        for pattern in iter_patterns(url_patterns):
            if pattern.name:
                self.stdout.write(pattern.name)
