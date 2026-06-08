"""
Management command for creating ModelMeta entries for multilabel classification models.

Supports two workflows:
1. Registering a local `.safetensors` weights file.
2. Generating metadata from a YAML template, downloading weights from Hugging Face.
"""

import logging
import tempfile
from collections.abc import Iterable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Protocol, TypedDict, cast

import yaml
from django.core.management import BaseCommand, CommandError, CommandParser

from endoreg_db.data import AI_MODEL_META_DATA_DIR
from endoreg_db.models import AiModel, LabelSet, ModelMeta

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "assets"
    / "colo_segmentation_RegNetX800MF_6.safetensors"
)

type CommandOptionValue = bool | int | str | None
type TemplateScalar = bool | int | float | str | None
type TemplateValue = TemplateScalar | list[TemplateScalar] | dict[str, TemplateValue]
type TemplateFields = dict[str, TemplateValue]


class TemplateEntry(TypedDict, total=False):
    fields: TemplateFields
    setup_config: dict[str, TemplateValue]


class ModelMetaKwargs(TypedDict, total=False):
    activation: str
    mean: str
    std: str
    size_x: int
    size_y: int
    axes: str
    batchsize: int
    num_workers: int
    description: str


class HfDownloadKwargs(TypedDict, total=False):
    repo_id: str
    filename: str
    local_dir: str
    local_dir_use_symlinks: bool
    token: str


class _HfHubDownload(Protocol):
    def __call__(
        self,
        *,
        repo_id: str,
        filename: str,
        local_dir: str,
        local_dir_use_symlinks: bool,
        token: str | None = None,
    ) -> str: ...


class _NamedLabelSet(Protocol):
    name: str
    version: int


class _NamedModelMetaModel(Protocol):
    name: str


class _CommandModelMeta(Protocol):
    name: str
    version: str
    model: _NamedModelMetaModel


hf_hub_download = cast(
    _HfHubDownload,
    getattr(import_module("huggingface_hub"), "hf_hub_download"),
)


class Command(BaseCommand):
    help = (
        "Create or update ModelMeta entries for multilabel classification models using "
        "either a local safetensor file or a YAML template with Hugging Face download support."
    )

    def add_arguments(self, parser: CommandParser) -> None:  # noqa: D401 - inherited docstring is sufficient
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
            help="Entry selector when the template file defines multiple models.",
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
            help="Description to store on the ModelMeta record.",
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
            help="Hugging Face token for private repositories.",
        )

    def handle(
        self,
        *args: str,
        **options: CommandOptionValue,
    ) -> None:  # noqa: D401 - inherited docstring is sufficient
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
            self.style.SUCCESS(self._model_meta_success_message(model_meta))
        )

    def _create_from_local_file(
        self,
        options: Mapping[str, CommandOptionValue],
    ) -> ModelMeta:
        weights_path = (
            Path(self._required_string_option(options, "model_path"))
            .expanduser()
            .resolve()
        )
        self._validate_safetensors_path(weights_path)

        model_name = self._required_string_option(options, "model_name")
        self._ensure_ai_model_exists(model_name)

        labelset = self._resolve_labelset(
            self._required_string_option(options, "image_classification_labelset_name"),
            options.get("image_classification_labelset_version"),
        )
        typed_labelset = cast(_NamedLabelSet, labelset)

        requested_version = options.get("model_meta_version") or "1"

        model_meta = ModelMeta.create_from_file(
            meta_name=model_name,
            model_name=model_name,
            labelset_name=typed_labelset.name,
            labelset_version=typed_labelset.version,
            weights_file=weights_path.as_posix(),
            requested_version=str(requested_version),
            bump_if_exists=self._bool_option(options, "bump_version"),
            **self._collect_local_kwargs(options),
        )

        return model_meta

    def _create_from_template(
        self,
        options: Mapping[str, CommandOptionValue],
    ) -> ModelMeta:
        template_path = self._resolve_template_path(options)
        entries = self._load_template_entries(template_path)
        entry = self._select_template_entry(entries, options)

        fields = entry.get("fields", {})
        if not fields:
            raise CommandError("Template entry is missing a 'fields' section.")

        meta_name = self._string_from_template_or_option(
            fields, options, "name", "model_name"
        )
        model_name = self._string_from_template_or_option(
            fields, options, "model", "model_name"
        )
        labelset_name = self._string_from_template_or_option(
            fields,
            options,
            "labelset",
            "image_classification_labelset_name",
        )
        labelset_version = fields.get(
            "labelset_version", options.get("image_classification_labelset_version")
        )

        self._ensure_ai_model_exists(model_name)
        labelset = self._resolve_labelset(labelset_name, labelset_version)
        typed_labelset = cast(_NamedLabelSet, labelset)

        requested_version = options.get("model_meta_version") or fields.get("version")
        if not requested_version:
            raise CommandError(
                "Provide --model_meta_version or include a 'version' in the template entry."
            )

        hf_config = self._huggingface_fallback_config(entry)
        repo_id = self._template_string(hf_config, "repo_id")
        filename = self._template_string(hf_config, "filename")

        if not repo_id or not filename:
            raise CommandError(
                "Template entry must define setup_config.huggingface_fallback.repo_id and filename for weight download."
            )

        if not filename.endswith(".safetensors"):
            raise CommandError(
                "Only .safetensors files are supported when downloading from Hugging Face."
            )

        token = options.get("huggingface_token")

        with tempfile.TemporaryDirectory(prefix="hf-multilabel-") as download_dir:
            weights_path = Path(
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=download_dir,
                    local_dir_use_symlinks=False,
                    token=token if isinstance(token, str) and token else None,
                )
            ).resolve()

            self._validate_safetensors_path(weights_path)

            model_meta = ModelMeta.create_from_file(
                meta_name=meta_name,
                model_name=model_name,
                labelset_name=typed_labelset.name,
                labelset_version=typed_labelset.version,
                weights_file=weights_path.as_posix(),
                requested_version=str(requested_version),
                bump_if_exists=self._bool_option(options, "bump_version"),
                **self._collect_template_kwargs(fields, options),
            )

        return model_meta

    def _resolve_template_path(self, options: Mapping[str, CommandOptionValue]) -> Path:
        template_path = options.get("template_path")
        template_name = options.get("template_name")

        if isinstance(template_path, str) and template_path:
            resolved = Path(template_path).expanduser().resolve()
        elif isinstance(template_name, str) and template_name:
            resolved = (AI_MODEL_META_DATA_DIR / f"{template_name}.yaml").resolve()
        else:  # pragma: no cover - guarded by caller
            raise CommandError(
                "Template mode requires --template_path or --template_name."
            )

        if not resolved.exists():
            raise CommandError(f"Template file not found: {resolved}")

        return resolved

    @staticmethod
    def _load_template_entries(template_path: Path) -> list[TemplateEntry]:
        with template_path.open("r", encoding="utf-8") as handle:
            data = cast(TemplateValue, yaml.safe_load(handle) or [])

        if isinstance(data, dict):
            return [cast(TemplateEntry, data)]
        if isinstance(data, list):
            return [
                cast(TemplateEntry, entry) for entry in data if isinstance(entry, dict)
            ]

        raise CommandError(
            f"Template {template_path} must define a mapping or list of mappings."
        )

    def _select_template_entry(
        self,
        entries: Iterable[TemplateEntry],
        options: Mapping[str, CommandOptionValue],
    ) -> TemplateEntry:
        target = options.get("template_entry_name") or options.get("model_name")

        for entry in entries:
            fields = entry.get("fields", {})
            if not fields:
                continue
            if target and (
                fields.get("name") == target or fields.get("model") == target
            ):
                return entry

        entries = list(entries)
        if len(entries) == 1:
            return entries[0]

        raise CommandError(
            "Unable to determine which template entry to use. Specify --template_entry_name to disambiguate."
        )

    def _collect_local_kwargs(
        self,
        options: Mapping[str, CommandOptionValue],
    ) -> ModelMetaKwargs:
        return self._filter_none(
            {
                "activation": self._string_option(options, "activation_function_name"),
                "mean": self._string_option(options, "mean"),
                "std": self._string_option(options, "std"),
                "size_x": self._int_option(options, "size_x"),
                "size_y": self._int_option(options, "size_y"),
                "axes": self._string_option(options, "axes"),
                "batchsize": self._int_option(options, "batchsize"),
                "num_workers": self._int_option(options, "num_workers"),
                "description": self._string_option(options, "description"),
            }
        )

    def _collect_template_kwargs(
        self,
        fields: TemplateFields,
        options: Mapping[str, CommandOptionValue],
    ) -> ModelMetaKwargs:
        return self._filter_none(
            {
                "activation": self._template_or_option_string(
                    fields, options, "activation", "activation_function_name"
                ),
                "mean": self._normalise_sequence(fields.get("mean"))
                or self._string_option(options, "mean"),
                "std": self._normalise_sequence(fields.get("std"))
                or self._string_option(options, "std"),
                "size_x": self._template_or_option_int(fields, options, "size_x"),
                "size_y": self._template_or_option_int(fields, options, "size_y"),
                "axes": self._template_or_option_string(
                    fields, options, "axes", "axes"
                ),
                "batchsize": self._template_or_option_int(fields, options, "batchsize"),
                "num_workers": self._template_or_option_int(
                    fields, options, "num_workers"
                ),
                "description": self._template_or_option_string(
                    fields, options, "description", "description"
                ),
            }
        )

    @staticmethod
    def _normalise_sequence(value: TemplateValue) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return ",".join(str(item) for item in value)
        return str(value)

    @staticmethod
    def _filter_none(payload: ModelMetaKwargs) -> ModelMetaKwargs:
        return cast(
            ModelMetaKwargs,
            {key: value for key, value in payload.items() if value not in (None, "")},
        )

    @staticmethod
    def _validate_safetensors_path(path: Path) -> None:
        if path.suffix != ".safetensors":
            raise CommandError(f"Expected a .safetensors file, got: {path}")
        if not path.exists():
            raise CommandError(f"Weights file not found: {path}")

    @staticmethod
    def _ensure_ai_model_exists(model_name: str) -> None:
        if not AiModel.objects.filter(name=model_name).exists():
            raise CommandError(
                f"AiModel not found: {model_name}. Load ai model data before running this command."
            )

    @staticmethod
    def _resolve_labelset(
        name: str, version: CommandOptionValue | TemplateValue
    ) -> LabelSet:
        queryset = LabelSet.objects.filter(name=name)

        if version in (None, -1):
            labelset = queryset.order_by("-version").first()
        else:
            labelset = queryset.filter(version=version).first()

        if not labelset:
            raise CommandError(
                f"LabelSet not found for name='{name}' and version='{version}'."
            )

        return labelset

    @staticmethod
    def _required_string_option(
        options: Mapping[str, CommandOptionValue],
        name: str,
    ) -> str:
        value = options[name]
        if not isinstance(value, str) or not value.strip():
            raise CommandError(f"Option {name} must be a non-empty string.")
        return value

    @staticmethod
    def _string_option(
        options: Mapping[str, CommandOptionValue],
        name: str,
    ) -> str:
        value = options.get(name, "")
        if value is None:
            return ""
        if not isinstance(value, str):
            raise CommandError(f"Option {name} must be a string.")
        return value

    @staticmethod
    def _int_option(
        options: Mapping[str, CommandOptionValue],
        name: str,
    ) -> int:
        value = options.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise CommandError(f"Option {name} must be an integer.")

    @staticmethod
    def _bool_option(
        options: Mapping[str, CommandOptionValue],
        name: str,
    ) -> bool:
        value = options.get(name, False)
        if not isinstance(value, bool):
            raise CommandError(f"Option {name} must be a boolean flag.")
        return value

    def _string_from_template_or_option(
        self,
        fields: TemplateFields,
        options: Mapping[str, CommandOptionValue],
        template_key: str,
        option_key: str,
    ) -> str:
        value = fields.get(template_key)
        if isinstance(value, str) and value.strip():
            return value
        return self._required_string_option(options, option_key)

    @staticmethod
    def _template_or_option_string(
        fields: TemplateFields,
        options: Mapping[str, CommandOptionValue],
        template_key: str,
        option_key: str,
    ) -> str:
        value = fields.get(template_key)
        if isinstance(value, str):
            return value
        return Command._string_option(options, option_key)

    @staticmethod
    def _template_or_option_int(
        fields: TemplateFields,
        options: Mapping[str, CommandOptionValue],
        key: str,
    ) -> int:
        value = fields.get(key)
        if value is None:
            return Command._int_option(options, key)
        if isinstance(value, bool):
            raise CommandError(f"Template value {key} must be an integer.")
        if isinstance(value, (int, float, str)):
            return int(value)
        raise CommandError(f"Template value {key} must be an integer.")

    @staticmethod
    def _template_string(fields: Mapping[str, TemplateValue], name: str) -> str:
        value = fields.get(name)
        if not isinstance(value, str) or not value.strip():
            raise CommandError(f"Template value {name} must be a non-empty string.")
        return value

    @staticmethod
    def _huggingface_fallback_config(entry: TemplateEntry) -> dict[str, TemplateValue]:
        setup_config = entry.get("setup_config", {})
        fallback = setup_config.get("huggingface_fallback")
        if not isinstance(fallback, dict):
            return {}
        return fallback

    @staticmethod
    def _model_meta_success_message(model_meta: ModelMeta) -> str:
        typed_model_meta = cast(_CommandModelMeta, model_meta)
        return (
            f"ModelMeta ready: {typed_model_meta.name} "
            f"(v{typed_model_meta.version}) for {typed_model_meta.model.name}"
        )
