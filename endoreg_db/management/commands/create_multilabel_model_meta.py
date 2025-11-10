"""
Management command for creating ModelMeta entries for multilabel classification models.

Supports two workflows:
1. Registering a local `.safetensors` weights file.
2. Generating metadata from a YAML template, downloading weights from Hugging Face.
"""

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml
from django.core.management import BaseCommand, CommandError
from huggingface_hub import hf_hub_download

from endoreg_db.data import AI_MODEL_META_DATA_DIR
from endoreg_db.models import AiModel, LabelSet, ModelMeta

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "assets"
    / "colo_segmentation_RegNetX800MF_6.safetensors"
)


class Command(BaseCommand):
    help = (
        "Create or update ModelMeta entries for multilabel classification models using "
        "either a local safetensor file or a YAML template with Hugging Face download support."
    )

    def add_arguments(self, parser):  # noqa: D401 - inherited docstring is sufficient
        parser.add_argument(
            "--model_name",
            type=str,
            default="image_multilabel_classification_colonoscopy_default",
            help="Name of the AiModel to attach metadata to.",
        )
        parser.add_argument(
            "--model_path",
            type=str,
            default=str(DEFAULT_MODEL_PATH),
            help=(
                "Path to a local .safetensors weights file. If provided (or left as default) "
                "the command registers the local weights."
            ),
        )
        parser.add_argument(
            "--template_path",
            type=str,
            default=None,
            help="Absolute or relative path to a model meta YAML template.",
        )
        parser.add_argument(
            "--template_name",
            type=str,
            default=None,
            help=(
                "Name of a built-in template file in endoreg_db/data/ai_model_meta (without extension)."
            ),
        )
        parser.add_argument(
            "--template_entry_name",
            type=str,
            default=None,
            help="Optional entry selector when the template file defines multiple models.",
        )
        parser.add_argument(
            "--model_meta_version",
            type=str,
            default=None,
            help=(
                "Version to assign to the metadata. When omitted the command uses the template value "
                "or defaults to '1' for local registrations."
            ),
        )
        parser.add_argument(
            "--image_classification_labelset_name",
            type=str,
            default="multilabel_classification_colonoscopy_default",
            help="Name of the LabelSet used by the model.",
        )
        parser.add_argument(
            "--image_classification_labelset_version",
            type=int,
            default=-1,
            help="Specific LabelSet version. Use -1 to select the latest available version.",
        )
        parser.add_argument(
            "--activation_function_name",
            type=str,
            default="sigmoid",
            help="Activation function applied to model outputs.",
        )
        parser.add_argument(
            "--mean",
            type=str,
            default="0.45211223,0.27139644,0.19264949",
            help="Comma-separated mean values for input normalization.",
        )
        parser.add_argument(
            "--std",
            type=str,
            default="0.31418097,0.21088019,0.16059452",
            help="Comma-separated std values for input normalization.",
        )
        parser.add_argument(
            "--size_x",
            type=int,
            default=716,
            help="Input width expected by the model.",
        )
        parser.add_argument(
            "--size_y",
            type=int,
            default=716,
            help="Input height expected by the model.",
        )
        parser.add_argument(
            "--axes",
            type=str,
            default="2,0,1",
            help="Comma-separated axis order expected by the model (e.g. '2,0,1' for CHW).",
        )
        parser.add_argument(
            "--batchsize",
            type=int,
            default=16,
            help="Default batch size for inference.",
        )
        parser.add_argument(
            "--num_workers",
            type=int,
            default=0,
            help="Default number of data loading workers.",
        )
        parser.add_argument(
            "--description",
            type=str,
            default="",
            help="Optional description to store on the ModelMeta record.",
        )
        parser.add_argument(
            "--bump_version",
            action="store_true",
            help="If the requested version exists, bump to the next available version instead of failing.",
        )
        parser.add_argument(
            "--huggingface_token",
            type=str,
            default=None,
            help="Optional Hugging Face token for private repositories.",
        )

    def handle(self, *args, **options):  # noqa: D401 - inherited docstring is sufficient
        use_template = options.get("template_path") or options.get("template_name")

        try:
            if use_template:
                model_meta = self._create_from_template(options)
            else:
                model_meta = self._create_from_local_file(options)
        except CommandError:
            raise
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to create ModelMeta", exc_info=exc)
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(f"ModelMeta ready: {model_meta.name} (v{model_meta.version}) for {model_meta.model.name}")
        )

    def _create_from_local_file(self, options: Dict[str, Any]) -> ModelMeta:
        weights_path = Path(options["model_path"]).expanduser().resolve()
        self._validate_safetensors_path(weights_path)

        model_name = options["model_name"]
        self._ensure_ai_model_exists(model_name)

        labelset = self._resolve_labelset(
            options["image_classification_labelset_name"],
            options.get("image_classification_labelset_version"),
        )

        requested_version = options.get("model_meta_version") or "1"

        model_meta = ModelMeta.create_from_file(
            meta_name=model_name,
            model_name=model_name,
            labelset_name=labelset.name,
            labelset_version=labelset.version,
            weights_file=weights_path.as_posix(),
            requested_version=str(requested_version),
            bump_if_exists=options.get("bump_version", False),
            **self._collect_local_kwargs(options),
        )

        return model_meta

    def _create_from_template(self, options: Dict[str, Any]) -> ModelMeta:
        template_path = self._resolve_template_path(options)
        entries = self._load_template_entries(template_path)
        entry = self._select_template_entry(entries, options)

        fields = entry.get("fields", {})
        if not fields:
            raise CommandError("Template entry is missing a 'fields' section.")

        meta_name = fields.get("name") or options["model_name"]
        model_name = fields.get("model") or options["model_name"]
        labelset_name = fields.get("labelset") or options["image_classification_labelset_name"]
        labelset_version = fields.get("labelset_version", options.get("image_classification_labelset_version"))

        self._ensure_ai_model_exists(model_name)
        labelset = self._resolve_labelset(labelset_name, labelset_version)

        requested_version = options.get("model_meta_version") or fields.get("version")
        if not requested_version:
            raise CommandError("Provide --model_meta_version or include a 'version' in the template entry.")

        hf_config = entry.get("setup_config", {}).get("huggingface_fallback", {})
        repo_id = hf_config.get("repo_id")
        filename = hf_config.get("filename")

        if not repo_id or not filename:
            raise CommandError(
                "Template entry must define setup_config.huggingface_fallback.repo_id and filename for weight download."
            )

        if not filename.endswith(".safetensors"):
            raise CommandError("Only .safetensors files are supported when downloading from Hugging Face.")

        token = options.get("huggingface_token")

        with tempfile.TemporaryDirectory(prefix="hf-multilabel-") as download_dir:
            download_kwargs = {
                "repo_id": repo_id,
                "filename": filename,
                "local_dir": download_dir,
                "local_dir_use_symlinks": False,
            }
            if token:
                download_kwargs["token"] = token

            weights_path = Path(hf_hub_download(**download_kwargs)).resolve()

            self._validate_safetensors_path(weights_path)

            model_meta = ModelMeta.create_from_file(
                meta_name=meta_name,
                model_name=model_name,
                labelset_name=labelset.name,
                labelset_version=labelset.version,
                weights_file=weights_path.as_posix(),
                requested_version=str(requested_version),
                bump_if_exists=options.get("bump_version", False),
                **self._collect_template_kwargs(fields, options),
            )

        return model_meta

    def _resolve_template_path(self, options: Dict[str, Any]) -> Path:
        template_path = options.get("template_path")
        template_name = options.get("template_name")

        if template_path:
            resolved = Path(template_path).expanduser().resolve()
        elif template_name:
            resolved = (AI_MODEL_META_DATA_DIR / f"{template_name}.yaml").resolve()
        else:  # pragma: no cover - guarded by caller
            raise CommandError("Template mode requires --template_path or --template_name.")

        if not resolved.exists():
            raise CommandError(f"Template file not found: {resolved}")

        return resolved

    @staticmethod
    def _load_template_entries(template_path: Path) -> List[Dict[str, Any]]:
        with template_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or []

        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]

        raise CommandError(f"Template {template_path} must define a mapping or list of mappings.")

    def _select_template_entry(self, entries: Iterable[Dict[str, Any]], options: Dict[str, Any]) -> Dict[str, Any]:
        target = options.get("template_entry_name") or options.get("model_name")

        for entry in entries:
            fields = entry.get("fields", {})
            if not fields:
                continue
            if target and (fields.get("name") == target or fields.get("model") == target):
                return entry

        entries = list(entries)
        if len(entries) == 1:
            return entries[0]

        raise CommandError(
            "Unable to determine which template entry to use. Specify --template_entry_name to disambiguate."
        )

    def _collect_local_kwargs(self, options: Dict[str, Any]) -> Dict[str, Any]:
        return self._filter_none(
            {
                "activation": options.get("activation_function_name"),
                "mean": options.get("mean"),
                "std": options.get("std"),
                "size_x": options.get("size_x"),
                "size_y": options.get("size_y"),
                "axes": options.get("axes"),
                "batchsize": options.get("batchsize"),
                "num_workers": options.get("num_workers"),
                "description": options.get("description"),
            }
        )

    def _collect_template_kwargs(self, fields: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
        def numeric(value):
            return int(value) if value is not None else value

        return self._filter_none(
            {
                "activation": fields.get("activation") or options.get("activation_function_name"),
                "mean": self._normalise_sequence(fields.get("mean")) or options.get("mean"),
                "std": self._normalise_sequence(fields.get("std")) or options.get("std"),
                "size_x": numeric(fields.get("size_x")) if fields.get("size_x") is not None else options.get("size_x"),
                "size_y": numeric(fields.get("size_y")) if fields.get("size_y") is not None else options.get("size_y"),
                "axes": fields.get("axes") or options.get("axes"),
                "batchsize": numeric(fields.get("batchsize")) if fields.get("batchsize") is not None else options.get("batchsize"),
                "num_workers": numeric(fields.get("num_workers")) if fields.get("num_workers") is not None else options.get("num_workers"),
                "description": fields.get("description") or options.get("description"),
            }
        )

    @staticmethod
    def _normalise_sequence(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return ",".join(str(item) for item in value)
        return str(value)

    @staticmethod
    def _filter_none(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in payload.items() if value not in (None, "")}

    @staticmethod
    def _validate_safetensors_path(path: Path) -> None:
        if path.suffix != ".safetensors":
            raise CommandError(f"Expected a .safetensors file, got: {path}")
        if not path.exists():
            raise CommandError(f"Weights file not found: {path}")

    @staticmethod
    def _ensure_ai_model_exists(model_name: str) -> None:
        if not AiModel.objects.filter(name=model_name).exists():
            raise CommandError(f"AiModel not found: {model_name}. Load ai model data before running this command.")

    @staticmethod
    def _resolve_labelset(name: str, version: Any) -> LabelSet:
        queryset = LabelSet.objects.filter(name=name)

        if version in (None, -1):
            labelset = queryset.order_by("-version").first()
        else:
            labelset = queryset.filter(version=version).first()

        if not labelset:
            raise CommandError(f"LabelSet not found for name='{name}' and version='{version}'.")

        return labelset
