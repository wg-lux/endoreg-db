"""Create a protected human-review shortlist without modifying annotations."""

from __future__ import annotations

import json
from collections.abc import Hashable
from pathlib import Path
from typing import Protocol, cast

import yaml
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts.ai_dataset import (
    AIDataSetActiveLearningCandidateContract,
    AIDataSetActiveLearningConfigContract,
)
from pydantic import TypeAdapter

from endoreg_db.services.aidataset_active_learning_shortlist import (
    build_active_learning_review_shortlist,
    write_active_learning_review_shortlist,
)
from endoreg_db.utils.paths import ensure_within_protected_root


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(
        self, node: yaml.Node, deep: bool = False
    ) -> dict[Hashable, object]:
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("Config must be a mapping.")
        keys: set[str] = set()
        for key, _value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.value in keys:
                raise ValueError("Config must contain unique scalar keys.")
            keys.add(key.value)
        return cast(dict[Hashable, object], super().construct_mapping(node, deep=deep))


class _YamlTokenScanner(Protocol):
    """Typed boundary for PyYAML's unannotated token scanner."""

    def check_token(self) -> bool: ...

    def get_token(self) -> yaml.Token | None: ...

    def dispose(self) -> None: ...


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Candidate JSON must not contain duplicate object keys.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError("Candidate JSON must contain only finite numbers.")


def _read_bounded(path: Path, maximum_bytes: int) -> str:
    if not path.is_file():
        raise ValueError("Active learning inputs must be regular files.")
    with path.open("rb") as handle:
        payload = handle.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise ValueError("Active learning input exceeds the supported document size.")
    return payload.decode("utf-8")


class Command(BaseCommand):
    help = "Prepare a protected active learning shortlist for human review; never trains or promotes models."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--candidates-json", type=Path, required=True)
        parser.add_argument("--config-yaml", type=Path, required=True)
        parser.add_argument("--output-json", type=Path, required=True)
        parser.add_argument("--dataset-id", type=int, required=True)
        parser.add_argument("--model-meta-id", type=int, required=True)

    def handle(self, *args: object, **options: object) -> None:
        try:
            candidate_path = options["candidates_json"]
            config_path = options["config_yaml"]
            output_path = options["output_json"]
            dataset_id = options["dataset_id"]
            model_meta_id = options["model_meta_id"]
            if not all(
                isinstance(path, Path)
                for path in (candidate_path, config_path, output_path)
            ):
                raise ValueError(
                    "Candidate, config and output arguments must be local paths."
                )
            if type(dataset_id) is not int or type(model_meta_id) is not int:
                raise ValueError("Dataset and model identities must be integers.")
            destination = ensure_within_protected_root(cast(Path, output_path))
            candidate_text = _read_bounded(cast(Path, candidate_path), 32 * 1024 * 1024)
            config_text = _read_bounded(cast(Path, config_path), 64 * 1024)
            raw_candidates: object = json.loads(
                candidate_text,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            scanner = cast(_YamlTokenScanner, yaml.SafeLoader(config_text))
            try:
                while scanner.check_token():
                    token = scanner.get_token()
                    if isinstance(
                        token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)
                    ):
                        raise ValueError(
                            "Active learning config must not contain YAML aliases or anchors."
                        )
            finally:
                scanner.dispose()
            raw_config: object = yaml.load(config_text, Loader=_UniqueKeyLoader)
            candidates = TypeAdapter(
                list[AIDataSetActiveLearningCandidateContract]
            ).validate_python(raw_candidates, strict=True)
            config = AIDataSetActiveLearningConfigContract.model_validate(
                raw_config, strict=True
            )
            shortlist = build_active_learning_review_shortlist(
                dataset_id=dataset_id,
                model_meta_id=model_meta_id,
                candidates=candidates,
                config=config,
            )
            write_active_learning_review_shortlist(shortlist, destination)
        except (
            OSError,
            ValueError,
            RuntimeError,
            yaml.YAMLError,
            ObjectDoesNotExist,
        ) as exc:
            raise CommandError(
                "Active learning shortlist rejected; verify input schema, dataset/model scope and protected output path."
            ) from exc
        self.stdout.write(
            "Human-review shortlist written. No annotations, training runs or models were modified."
        )
