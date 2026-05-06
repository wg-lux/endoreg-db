from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from django.urls import URLPattern, URLResolver, get_resolver


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "frontend_agent_url_contract.md"

TABLE_START = "<!-- BEGIN FRONTEND AGENT ROUTE TABLE -->"
TABLE_END = "<!-- END FRONTEND AGENT ROUTE TABLE -->"

ROUTE_ROW_RE = re.compile(r"^\|\s*`[^`]*`\s*\|\s*`(?P<path>/api/[^`]*)`\s*\|")
ENDPOINTS_RE = re.compile(
    r"export const endpoints = (?P<body>.*?)\n} as const",
    re.DOTALL,
)
SINGLE_QUOTED_RE = re.compile(r"'([^'\\]*(?:\\.[^'\\]*)*)'")
TEMPLATE_LITERAL_RE = re.compile(r"`([^`]*)`")
PLACEHOLDER_RE = re.compile(r"\{[^}]+\}")
TEMPLATE_EXPR_RE = re.compile(r"\$\{[^}]+\}")

EXCLUDED_PATTERN_PARTS = (
    "<drf_format_suffix:format>",
    "(?P<format>",
    "^protected_media",
    "^static",
    "api/^protected_media",
    "api/^static",
)


def _contract_text() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def _generic_route(path: str) -> str:
    return PLACEHOLDER_RE.sub("{param}", path)


def _extract_table_block(text: str) -> str:
    try:
        return text.split(TABLE_START, 1)[1].split(TABLE_END, 1)[0]
    except IndexError as exc:
        raise AssertionError("frontend route table markers are missing") from exc


def _documented_routes() -> set[str]:
    routes = set()
    for line in _extract_table_block(_contract_text()).splitlines():
        match = ROUTE_ROW_RE.match(line)
        if match:
            routes.add(_generic_route(match.group("path")))

    assert routes, "frontend route table did not contain any `/api/` routes"
    return routes


def _iter_url_patterns(
    patterns: Iterable[URLPattern | URLResolver],
    prefix: str = "",
) -> Iterable[str]:
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLPattern):
            yield route
        elif isinstance(pattern, URLResolver):
            yield from _iter_url_patterns(pattern.url_patterns, route)


def _normalize_django_route(route: str) -> str | None:
    if any(part in route for part in EXCLUDED_PATTERN_PARTS):
        return None
    if not route.startswith("api/"):
        return None

    relative_route = route.removeprefix("api/")
    if relative_route.startswith("^"):
        relative_route = relative_route[1:]
    if relative_route.endswith("$"):
        relative_route = relative_route[:-1]

    relative_route = re.sub(
        r"\(\?P<([a-zA-Z_][a-zA-Z0-9_]*)>\[\^/\.\]\+\)",
        r"{\1}",
        relative_route,
    )
    relative_route = re.sub(
        r"<(?:int|str|uuid):([a-zA-Z_][a-zA-Z0-9_]*)>",
        r"{\1}",
        relative_route,
    )
    relative_route = relative_route.replace(r"\.", ".").replace("\\", "")

    return _generic_route(f"/api/{relative_route}")


def _actual_routes() -> set[str]:
    routes = set()
    for route in _iter_url_patterns(get_resolver().url_patterns):
        normalized_route = _normalize_django_route(route)
        if normalized_route:
            routes.add(normalized_route)

    assert routes, "Django resolver did not expose any `/api/` routes"
    return routes


def _strip_line_comments(body: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in body.splitlines())


def _endpoint_literal_to_route(value: str) -> str | None:
    route = TEMPLATE_EXPR_RE.sub("{param}", value).split("?", 1)[0]
    if route == "":
        return "/api/"
    if "/" not in route:
        return None
    if route.startswith("/api/"):
        return _generic_route(route)
    return _generic_route(f"/api/{route}")


def _endpoint_routes() -> set[str]:
    match = ENDPOINTS_RE.search(_contract_text())
    assert match, "could not locate `export const endpoints` in frontend contract"

    body = _strip_line_comments(match.group("body"))
    routes = set()
    for regex in (SINGLE_QUOTED_RE, TEMPLATE_LITERAL_RE):
        for value in regex.findall(body):
            route = _endpoint_literal_to_route(value)
            if route:
                routes.add(route)

    assert routes, "endpoint helper map did not contain any routes"
    return routes


def _route_diff_message(*, expected: set[str], actual: set[str]) -> str:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return (
        "route contract drift detected\n"
        f"missing:\n{chr(10).join(missing) or '-'}\n"
        f"extra:\n{chr(10).join(extra) or '-'}"
    )


def test_complete_route_table_matches_django_resolver() -> None:
    actual_routes = _actual_routes()
    documented_routes = _documented_routes()

    assert documented_routes == actual_routes, _route_diff_message(
        expected=actual_routes,
        actual=documented_routes,
    )


def test_endpoint_map_matches_complete_route_table() -> None:
    documented_routes = _documented_routes()
    endpoint_routes = _endpoint_routes()

    assert endpoint_routes == documented_routes, _route_diff_message(
        expected=documented_routes,
        actual=endpoint_routes,
    )
