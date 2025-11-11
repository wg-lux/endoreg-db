from __future__ import annotations

import argparse
import json
import math
import os
import time
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import googlemaps
import pandas as pd
from icecream import ic

from scripts.ntx_data.utils.utils import processed_data_dir


reference_address = "Oberdürrbacher Str. 6, 97080 Würzburg, Deutschland"
patient_df_path = processed_data_dir / "patient_df.csv"
output_path = processed_data_dir / "unique_post_codes.csv"
cache_path = processed_data_dir / "post_code_distances_cache.json"
_INVALID_TOKENS = {"", "na", "nan", "none", "null", "n/a"}


def _format_numeric_post_code(value: int) -> Optional[str]:
    if value <= 0:
        return None
    digits = f"{value:d}"
    if len(digits) > 5:
        digits = digits[:5]
    return digits.zfill(5)


def _normalize_post_code(value: object) -> Optional[str]:
    """Return a five digit postal code string or None if value is unusable."""

    if value is None:
        return None

    if isinstance(value, float):
        if math.isnan(value):
            return None
        formatted = _format_numeric_post_code(int(round(value)))
        if formatted:
            return formatted
        return None

    if isinstance(value, int):
        formatted = _format_numeric_post_code(value)
        if formatted:
            return formatted
        return None

    normalized = str(value).strip().replace('"', "").replace("'", "")
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered in _INVALID_TOKENS:
        return None

    candidate = normalized.replace(" ", "")
    candidate = candidate.replace(",", ".")

    try:
        numeric_candidate = float(candidate)
    except ValueError:
        numeric_candidate = None

    if numeric_candidate is not None and numeric_candidate > 0 and numeric_candidate.is_integer():
        formatted = _format_numeric_post_code(int(numeric_candidate))
        if formatted:
            return formatted

    digits_only = ''.join(ch for ch in candidate if ch.isdigit())
    if not digits_only:
        return None

    if len(digits_only) > 5:
        digits_only = digits_only[:5]

    digits_only = digits_only.zfill(5)
    if digits_only == "00000":
        return None

    return digits_only


def _load_cache() -> Dict[str, Dict[str, Optional[float]]]:
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as cache_file:
                raw_cache = json.load(cache_file)
        except json.JSONDecodeError:
            ic("Cache file is corrupted; rebuilding cache.")
        else:
            cleaned: Dict[str, Dict[str, Optional[float]]] = {}
            for key, value in raw_cache.items():
                normalized_key = _normalize_post_code(key)
                if not normalized_key:
                    continue
                cleaned[normalized_key] = value
            return cleaned
    return {}


def _store_cache(cache: Dict[str, Dict[str, Optional[float]]]) -> None:
    with open(cache_path, "w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, indent=2)


def _get_reference_location(client: Any) -> Tuple[float, float]:
    geocode = client.geocode(reference_address)
    if not geocode:
        raise RuntimeError("Unable to geocode reference address.")
    location = geocode[0]["geometry"]["location"]
    return location["lat"], location["lng"]


def _haversine_km(origin: Tuple[float, float], destination: Tuple[float, float]) -> float:
    lat1, lon1 = origin
    lat2, lon2 = destination
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def _round_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 3)


def _compute_distances(
    client: Any,
    origin_coords: Tuple[float, float],
    post_code: str
) -> Dict[str, Optional[float]]:
    destination_query = f"{post_code}, Germany"
    distances_km: List[float] = []
    destination_coords: Optional[Tuple[float, float]] = None

    try:
        directions = client.directions(
            origin=reference_address,
            destination=destination_query,
            mode="driving",
            alternatives=True
        )
        for route in directions:
            legs = route.get("legs") or []
            if not legs:
                continue
            leg = legs[0]
            distance_info = leg.get("distance")
            if distance_info and "value" in distance_info:
                distances_km.append(distance_info["value"] / 1000.0)
            if destination_coords is None:
                end_location = leg.get("end_location")
                if end_location:
                    destination_coords = (end_location.get("lat"), end_location.get("lng"))
    except Exception as exc:  # noqa: BLE001
        ic(f"Failed to fetch driving directions for {post_code}: {exc}")

    if destination_coords is None:
        geocode_result = client.geocode(destination_query)
        if geocode_result:
            geometry = geocode_result[0].get("geometry", {})
            location = geometry.get("location")
            if location:
                destination_coords = (location.get("lat"), location.get("lng"))

    distances_km.sort()
    driving_values: List[Optional[float]] = [None, None, None]
    for idx, distance in enumerate(distances_km[:3]):
        driving_values[idx] = distance

    mean_distance = mean(distances_km) if distances_km else None
    median_distance = median(distances_km) if distances_km else None

    geographic_distance = None
    if destination_coords and all(coord is not None for coord in destination_coords):
        geographic_distance = _haversine_km(origin_coords, destination_coords)  # straight-line distance

    return {
    "distance_car_1": _round_or_none(driving_values[0]),
    "distance_car_2": _round_or_none(driving_values[1]),
    "distance_car_3": _round_or_none(driving_values[2]),
        "distance_car_mean": _round_or_none(mean_distance),
        "distance_car_median": _round_or_none(median_distance),
        "distance_geographic": _round_or_none(geographic_distance),
    }


def _select_post_codes(
    candidates: Sequence[str],
    limit: Optional[int]
) -> List[str]:
    if limit is None:
        return list(candidates)
    return list(candidates)[:limit]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute travel distances for NTX patient post codes.")
    parser.add_argument(
        "--post-code",
        dest="post_codes",
        nargs="*",
        help="Optional list of explicit post codes to process. If omitted, uses all post codes from patient_df.csv.",
    )
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=None,
        help="Maximum number of post codes to process (after filtering and sorting).",
    )
    parser.add_argument(
        "--skip-cache",
        dest="skip_cache",
        action="store_true",
        help="Ignore cached results and fetch fresh data from the Google Maps API.",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        default=str(output_path),
        help="Destination CSV path for the enriched data (default: data/ntx-data/clean/unique_post_codes.csv).",
    )
    return parser.parse_args()


def _normalize_inputs(values: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    for raw in values:
        value = _normalize_post_code(raw)
        if value:
            normalized.append(value)
        else:
            ic(f"Skipping invalid post code input: {raw}")
    return sorted(set(normalized))


def main() -> None:
    args = _parse_args()
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY environment variable is not set.")

    client = googlemaps.Client(key=api_key)
    origin_coords = _get_reference_location(client)

    if args.post_codes:
        unique_post_codes = _normalize_inputs(args.post_codes)
    else:
        patient_df = pd.read_csv(patient_df_path)
        normalized_post_codes = {
            code
            for code in (
                _normalize_post_code(value) for value in patient_df["post_code"].unique()
            )
            if code
        }
        unique_post_codes = sorted(normalized_post_codes)
        ic(f"Found {len(unique_post_codes)} unique post codes")

    if args.limit is not None:
        unique_post_codes = _select_post_codes(unique_post_codes, args.limit)

    cache = {} if args.skip_cache else _load_cache()
    results: List[Dict[str, object]] = []

    for index, post_code in enumerate(unique_post_codes, start=1):
        cached_entry = cache.get(post_code)
        if cached_entry and not args.skip_cache:
            ic(f"[{index}/{len(unique_post_codes)}] Using cached distances for {post_code}")
            result = dict(cached_entry)
        else:
            ic(f"[{index}/{len(unique_post_codes)}] Fetching distances for {post_code}")
            result = _compute_distances(client, origin_coords, post_code)
            cache[post_code] = result
            _store_cache(cache)
            time.sleep(0.1)

        results.append({"post_code": post_code, **result})

    output_df = pd.DataFrame(results)
    output_csv_path = os.fspath(args.output_path)
    output_df.to_csv(output_csv_path, index=False)
    ic(f"Saved post code distances to {output_csv_path}")


if __name__ == "__main__":
    main()

