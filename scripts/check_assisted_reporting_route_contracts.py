#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
import sys
from typing import cast

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = PROJECT_ROOT / "quality" / "assisted_reporting_route_contracts.yml"
# TODO: When you run this change to your local paths
DEFAULT_LX_DTYPES_ROOT = Path("/home/admin/lx-data-models")
DEFAULT_LX_ANNOTATE_ROOT = Path("/home/admin/dev/lx-annotate")
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
ROUTE_PARAMETER = re.compile(r"\{([^{}]+)\}")


class ContractError(ValueError):
    """The assisted-reporting ownership contract is incomplete or has drifted."""


class RepositoryOwner(StrEnum):
    LX_DTYPES = "lx_dtypes"


@dataclass(frozen=True, slots=True)
class WireContract:
    backend_fields: str
    frontend_response_fields: str
    request_conversion_owner: str
    response_conversion_owner: str


@dataclass(frozen=True, slots=True)
class RouteContract:
    id: str
    method: str
    path: str
    owner_repository: RepositoryOwner
    implementation_file: str
    implementation_marker: str
    frontend_file: str
    frontend_markers: tuple[str, ...]
    query_parameters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssistedReportingContract:
    schema_version: str
    wire_contract: WireContract
    routes: tuple[RouteContract, ...]
    forbidden_frontend_markers: tuple[str, ...]


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError(f"{path} must be a mapping")
    return cast(dict[str, object], value)


def _exact_keys(data: dict[str, object], expected: set[str], path: str) -> None:
    if set(data) != expected:
        raise ContractError(
            f"{path} keys differ: expected {sorted(expected)}, got {sorted(data)}"
        )


def _string(data: dict[str, object], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{path}.{key} must be a non-empty string")
    return value


def _strings(data: dict[str, object], key: str, path: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ContractError(f"{path}.{key} must be a string list")
    return tuple(cast(list[str], value))


def parse_contract(value: object) -> AssistedReportingContract:
    root = _mapping(value, "contract")
    _exact_keys(
        root,
        {"schema_version", "wire_contract", "routes", "forbidden_frontend_markers"},
        "contract",
    )
    schema_version = _string(root, "schema_version", "contract")
    if schema_version != "1.0":
        raise ContractError(f"unsupported schema_version: {schema_version}")

    wire_data = _mapping(root["wire_contract"], "wire_contract")
    wire_keys = {
        "backend_fields",
        "frontend_response_fields",
        "request_conversion_owner",
        "response_conversion_owner",
    }
    _exact_keys(wire_data, wire_keys, "wire_contract")
    wire = WireContract(
        **{key: _string(wire_data, key, "wire_contract") for key in wire_keys}
    )
    if (wire.backend_fields, wire.frontend_response_fields) != (
        "snake_case",
        "camelCase",
    ):
        raise ContractError(
            "wire contract must map backend snake_case to frontend camelCase"
        )
    if wire.request_conversion_owner != wire.response_conversion_owner:
        raise ContractError(
            "request and response conversion must have one frontend owner"
        )

    raw_routes = root["routes"]
    if not isinstance(raw_routes, list) or not raw_routes:
        raise ContractError("routes must be a non-empty list")
    route_keys = {
        "id",
        "method",
        "path",
        "owner_repository",
        "implementation_file",
        "implementation_marker",
        "frontend_file",
        "frontend_markers",
        "query_parameters",
    }
    routes: list[RouteContract] = []
    seen_ids: set[str] = set()
    seen_routes: set[tuple[str, str]] = set()
    for index, route_value in enumerate(raw_routes):
        path_label = f"routes[{index}]"
        route_data = _mapping(route_value, path_label)
        _exact_keys(route_data, route_keys, path_label)
        route_id = _string(route_data, "id", path_label)
        method = _string(route_data, "method", path_label).upper()
        path = _string(route_data, "path", path_label)
        if route_id in seen_ids or (method, path) in seen_routes:
            raise ContractError(f"duplicate route contract: {route_id} {method} {path}")
        if method != "GET" or not path.startswith("/dtypes-api/"):
            raise ContractError(
                f"{path_label} must describe a canonical GET /dtypes-api/ route"
            )
        query_parameters = _strings(route_data, "query_parameters", path_label)
        names = (*ROUTE_PARAMETER.findall(path), *query_parameters)
        if any(SNAKE_CASE.fullmatch(name) is None for name in names):
            raise ContractError(f"{path_label} has a non-snake_case wire parameter")
        try:
            owner = RepositoryOwner(_string(route_data, "owner_repository", path_label))
        except ValueError as exc:
            raise ContractError(
                f"{path_label} has an unsupported owner_repository"
            ) from exc
        routes.append(
            RouteContract(
                id=route_id,
                method=method,
                path=path,
                owner_repository=owner,
                implementation_file=_string(
                    route_data, "implementation_file", path_label
                ),
                implementation_marker=_string(
                    route_data, "implementation_marker", path_label
                ),
                frontend_file=_string(route_data, "frontend_file", path_label),
                frontend_markers=_strings(route_data, "frontend_markers", path_label),
                query_parameters=query_parameters,
            )
        )
        seen_ids.add(route_id)
        seen_routes.add((method, path))
    return AssistedReportingContract(
        schema_version=schema_version,
        wire_contract=wire,
        routes=tuple(routes),
        forbidden_frontend_markers=_strings(
            root, "forbidden_frontend_markers", "contract"
        ),
    )


def load_contract(path: Path) -> AssistedReportingContract:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot load contract {path}: {exc}") from exc
    return parse_contract(raw)


def _require_markers(root: Path, relative_path: str, markers: tuple[str, ...]) -> None:
    path = root / relative_path
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read governed source {path}: {exc}") from exc
    missing = tuple(marker for marker in markers if marker not in source)
    if missing:
        raise ContractError(f"{path} is missing contract markers: {missing}")


def check_contract(
    contract: AssistedReportingContract,
    *,
    endoreg_root: Path,
    lx_dtypes_root: Path,
    lx_annotate_root: Path,
) -> None:
    conversion_file = contract.wire_contract.request_conversion_owner
    _require_markers(
        lx_annotate_root,
        conversion_file,
        ("localSnakecaseKeys", "camelcaseKeys(data, { deep: true })"),
    )
    _require_markers(
        endoreg_root,
        "endoreg_db/root_urls.py",
        ("DTYPES_API_PREFIX", "_dtypes_api_urlconf()"),
    )
    for route in contract.routes:
        _require_markers(
            lx_dtypes_root,
            route.implementation_file,
            (route.implementation_marker,),
        )
        _require_markers(lx_annotate_root, route.frontend_file, route.frontend_markers)

    frontend_source_root = lx_annotate_root / "frontend" / "src"
    for path in frontend_source_root.rglob("*"):
        if path.suffix not in {".ts", ".vue"} or "__tests__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for marker in contract.forbidden_frontend_markers:
            if marker in source:
                raise ContractError(f"forbidden frontend marker {marker!r} in {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--endoreg-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--lx-dtypes-root", type=Path, default=DEFAULT_LX_DTYPES_ROOT)
    parser.add_argument(
        "--lx-annotate-root", type=Path, default=DEFAULT_LX_ANNOTATE_ROOT
    )
    args = parser.parse_args(argv)
    try:
        contract = load_contract(args.contract)
        check_contract(
            contract,
            endoreg_root=args.endoreg_root,
            lx_dtypes_root=args.lx_dtypes_root,
            lx_annotate_root=args.lx_annotate_root,
        )
    except ContractError as exc:
        print(f"[assisted-reporting-contract] ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"[assisted-reporting-contract] verified {len(contract.routes)} canonical routes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
