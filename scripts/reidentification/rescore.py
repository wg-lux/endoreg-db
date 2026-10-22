"""Recompute scoring from finished attacker outputs, without running an attack."""

from __future__ import annotations

import argparse
import json
from pydantic import TypeAdapter

from scripts.reidentification.run import Score, Truth, initialize, score
from scripts.reidentification.worker import Bundle, Result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    args = parser.parse_args()
    initialize(args.runtime)
    from endoreg_db.utils.paths import get_runtime_paths

    root = get_runtime_paths().export_dir / "reidentification"
    scored: list[dict[str, object]] = []
    for result_path in sorted((root / "public").glob("*-result.json")):
        name = result_path.name.removesuffix("-result.json")
        result = Result.model_validate_json(result_path.read_text())
        baseline = Result.model_validate_json(
            (root / "public" / (name + "-baseline.json")).read_text()
        )
        bundle = Bundle.model_validate_json(
            (root / "private" / (name + "-attacker.json")).read_text()
        )
        truth = Truth.model_validate_json(
            (root / "private" / (name + "-truth.json")).read_text()
        )
        scored.append(
            score(
                int(name.split("-", 1)[0]), bundle, baseline, result, truth
            ).model_dump()
        )
    # Compare serialized rows as a set, independently of glob/production loop order.
    expected = TypeAdapter(list[Score]).validate_json(
        (root / "public" / "scores.json").read_text()
    )
    actual_rows = {json.dumps(row, sort_keys=True) for row in scored}
    expected_rows = {json.dumps(row.model_dump(), sort_keys=True) for row in expected}
    if actual_rows != expected_rows:
        raise AssertionError("Archived scoring differs")
    print(f"Verified {len(scored)} independently rescored cases")


if __name__ == "__main__":
    main()
