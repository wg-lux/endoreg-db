from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import NoneType
from typing import Literal, Protocol, TypeAlias, TypedDict, Unpack, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, transaction
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase

from ...data import (
    EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR,
    EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR,
    EXAMINATION_INDICATION_DATA_DIR,
)
from ...models import (
    Examination,
    ExaminationIndication,
    ExaminationIndicationClassification,
    ExaminationIndicationClassificationChoice,
    FindingIntervention,
    InformationSource,
)
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata

NullValue: TypeAlias = NoneType
TextOrNull: TypeAlias = str | NullValue
StringListSource: TypeAlias = str | Sequence[str]
LoadExaminationIndicationSource: TypeAlias = Literal["yaml", "dtypes", "hybrid"]

IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    ExaminationIndicationClassificationChoice.__name__,
    ExaminationIndicationClassification.__name__,
    ExaminationIndication.__name__,
]

SOURCE_YAML: LoadExaminationIndicationSource = "yaml"
SOURCE_DTYPES: LoadExaminationIndicationSource = "dtypes"
SOURCE_HYBRID: LoadExaminationIndicationSource = "hybrid"
SOURCE_CHOICES: list[LoadExaminationIndicationSource] = [
    SOURCE_YAML,
    SOURCE_DTYPES,
    SOURCE_HYBRID,
]

DEFAULT_DTYPES_MODULE = "lx_examinations"


class LoadExaminationIndicationCommandOptions(TypedDict):
    verbose: bool
    source: LoadExaminationIndicationSource
    module_name: str


class NamedRecord(Protocol):
    name: str


class DescriptionRecord(Protocol):
    description: TextOrNull

    def save(self, *, update_fields: list[str]) -> None: ...


class _FindingInterventionRelation(Protocol):
    def set(self, objs: Sequence[FindingIntervention]) -> None: ...


class _ExaminationIndicationClassificationRelation(Protocol):
    def set(self, objs: Sequence[ExaminationIndicationClassification]) -> None: ...


class _ExaminationIndicationRelation(Protocol):
    def set(self, objs: Sequence[ExaminationIndication]) -> None: ...


class _DtypesIndicationRecord(Protocol):
    expected_interventions: _FindingInterventionRelation
    classifications: _ExaminationIndicationClassificationRelation


class _DtypesExaminationRecord(Protocol):
    indications: _ExaminationIndicationRelation


IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    ExaminationIndication.__name__: {
        "dir": EXAMINATION_INDICATION_DATA_DIR,
        "model": ExaminationIndication,
        "foreign_keys": [
            "expected_interventions",
            "classifications",
            "information_sources",
        ],
        "foreign_key_models": [
            FindingIntervention,
            ExaminationIndicationClassification,
            InformationSource,
        ],
    },
    ExaminationIndicationClassification.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR,
        "model": ExaminationIndicationClassification,
        "foreign_keys": [
            "choices",  # This is a many-to-many field
        ],
        "foreign_key_models": [
            ExaminationIndicationClassificationChoice,
        ],
    },
    ExaminationIndicationClassificationChoice.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR,
        "model": ExaminationIndicationClassificationChoice,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_dtypes_data_dirs() -> list[Path]:
    """
    Return candidate lx_dtypes data roots (repo-local first, package fallback).
    """
    candidates: list[Path] = []

    repo_data_dir = _project_root() / "lx-data-models" / "lx_dtypes" / "data"
    if repo_data_dir.exists():
        candidates.append(repo_data_dir)

    try:
        import lx_dtypes

        package_data_dir = Path(lx_dtypes.__file__).resolve().parent / "data"
        if package_data_dir.exists():
            candidates.append(package_data_dir)
    except Exception:
        pass

    legacy_cwd_dir = Path("./lx_dtypes/data").resolve()
    if legacy_cwd_dir.exists():
        candidates.append(legacy_cwd_dir)

    deduplicated: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduplicated.append(resolved)
    return deduplicated


def _as_str_list(raw_value: StringListSource) -> list[str]:
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            return []
        return [part.strip() for part in value.split(",") if part.strip()]
    return [item.strip() for item in raw_value if item.strip()]


def _load_dtypes_knowledge_base(module_name: str) -> KnowledgeBase:
    from lx_dtypes.models.interface.DataLoader import DataLoader

    input_dirs = _resolve_dtypes_data_dirs()
    if not input_dirs:
        raise ValueError(
            "Could not find a lx_dtypes data directory. "
            "Expected either ./lx-data-models/lx_dtypes/data or package data."
        )

    loader = DataLoader(input_dirs=input_dirs)
    loader.load_module_configs()
    return loader.load_knowledge_base(module_name)


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Add the --verbose flag to the command-line argument parser.

        This method augments the parser with a '--verbose' option to enable detailed output
        during command execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )
        parser.add_argument(
            "--source",
            type=str,
            default=SOURCE_HYBRID,
            choices=SOURCE_CHOICES,
            help="Choose import source: yaml, dtypes, or hybrid (yaml + dtypes overlay).",
        )
        parser.add_argument(
            "--module-name",
            type=str,
            default=DEFAULT_DTYPES_MODULE,
            help="lx_dtypes module name used when source includes dtypes.",
        )

    def _load_from_yaml(self, verbose: bool) -> None:
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)

    def _required_tables_available(self, *, verbose: bool) -> bool:
        existing_tables = set(connection.introspection.table_names())
        required_tables = {
            model._meta.db_table
            for model in (
                Examination,
                ExaminationIndication,
                ExaminationIndicationClassification,
                FindingIntervention,
                InformationSource,
            )
        }
        missing_tables = sorted(required_tables - existing_tables)
        if not missing_tables:
            return True

        if verbose:
            self.stdout.write(
                self.style.WARNING(
                    "[dtypes] Skipping load because database tables are not available yet: "
                    + ", ".join(missing_tables)
                )
            )
        return False

    def _upsert_dtypes_indications(
        self,
        *,
        kb: KnowledgeBase,
        verbose: bool,
    ) -> None:
        indication_types_by_name = kb.indication_type
        indications_by_name = kb.indication

        for indication_name, indication in indications_by_name.items():
            description: TextOrNull = indication.description.strip() or None
            db_indication: ExaminationIndication
            db_indication, _created = ExaminationIndication.objects.get_or_create(
                name=indication_name,
                defaults={"description": description},
            )
            db_indication_description = cast(DescriptionRecord, db_indication)
            if db_indication_description.description != description:
                db_indication_description.description = description
                db_indication_description.save(update_fields=["description"])

            intervention_names = _as_str_list(indication.interventions)
            interventions: list[FindingIntervention] = list(
                FindingIntervention.objects.filter(name__in=intervention_names)
            )
            cast(
                _DtypesIndicationRecord, db_indication
            ).expected_interventions.set(interventions)

            found_intervention_names: set[str] = {
                cast(NamedRecord, intervention).name for intervention in interventions
            }
            missing_interventions = sorted(
                set(intervention_names) - found_intervention_names
            )
            if verbose and missing_interventions:
                self.stdout.write(
                    self.style.WARNING(
                        f"[dtypes] Indication '{indication_name}' references missing "
                        f"interventions: {', '.join(missing_interventions)}"
                    )
                )

            classification_names = _as_str_list(indication.indication_types)
            classifications: list[ExaminationIndicationClassification] = []
            for classification_name in classification_names:
                indication_type = indication_types_by_name.get(classification_name)
                classification_description: TextOrNull = None
                if indication_type is not None:
                    classification_description = (
                        indication_type.description.strip() or None
                    )

                classification: ExaminationIndicationClassification
                classification, _ = (
                    ExaminationIndicationClassification.objects.get_or_create(
                        name=classification_name,
                        defaults={"description": classification_description},
                    )
                )
                classification_description_record = cast(
                    DescriptionRecord, classification
                )
                if (
                    classification_description_record.description
                    != classification_description
                ):
                    classification_description_record.description = (
                        classification_description
                    )
                    classification_description_record.save(
                        update_fields=["description"]
                    )
                classifications.append(classification)

            cast(_DtypesIndicationRecord, db_indication).classifications.set(
                classifications
            )

        if verbose:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[dtypes] Upserted {len(indications_by_name)} indication records."
                )
            )

    def _sync_dtypes_examination_links(
        self, *, kb: KnowledgeBase, verbose: bool
    ) -> None:
        examinations_by_name = kb.examination
        if not examinations_by_name:
            if verbose:
                self.stdout.write(
                    self.style.WARNING(
                        "[dtypes] No examination records found; skipped indication link sync."
                    )
                )
            return

        updated_exam_count = 0
        missing_exam_names: list[str] = []
        for exam_name, dtypes_examination in examinations_by_name.items():
            db_examination = Examination.objects.filter(name=exam_name).first()
            if db_examination is None:
                missing_exam_names.append(exam_name)
                continue

            indication_names = _as_str_list(dtypes_examination.indications)
            indication_qs = ExaminationIndication.objects.filter(
                name__in=indication_names
            )
            cast(_DtypesExaminationRecord, db_examination).indications.set(
                list(indication_qs)
            )
            updated_exam_count += 1

            found_indication_names: set[str] = {
                str(indication_name)
                for indication_name in indication_qs.values_list("name", flat=True)
            }
            missing_indications = sorted(set(indication_names) - found_indication_names)
            if verbose and missing_indications:
                self.stdout.write(
                    self.style.WARNING(
                        f"[dtypes] Examination '{exam_name}' references missing "
                        f"indications: {', '.join(missing_indications)}"
                    )
                )

        if verbose and missing_exam_names:
            self.stdout.write(
                self.style.WARNING(
                    "[dtypes] Examinations not found in endoreg_db and not linked: "
                    + ", ".join(sorted(missing_exam_names))
                )
            )
        if verbose:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[dtypes] Synced indication links for {updated_exam_count} examinations."
                )
            )

    def _load_from_dtypes(
        self,
        *,
        verbose: bool,
        module_name: str,
        strict: bool,
    ) -> None:
        if not self._required_tables_available(verbose=verbose):
            return

        try:
            kb = _load_dtypes_knowledge_base(module_name)
        except Exception as exc:
            message = f"Failed loading indication catalog from lx_dtypes module '{module_name}': {exc}"
            if strict:
                raise CommandError(message) from exc
            self.stdout.write(
                self.style.WARNING(f"{message}. Continuing with YAML-only data.")
            )
            return

        with transaction.atomic():
            self._upsert_dtypes_indications(kb=kb, verbose=verbose)
            self._sync_dtypes_examination_links(kb=kb, verbose=verbose)

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadExaminationIndicationCommandOptions],
    ) -> None:
        """
        Load indication catalog data from yaml, dtypes, or both.
        """
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        source = options["source"]
        module_name = options["module_name"]

        if source in (SOURCE_YAML, SOURCE_HYBRID):
            self._load_from_yaml(verbose)
        if source in (SOURCE_DTYPES, SOURCE_HYBRID):
            self._load_from_dtypes(
                verbose=verbose,
                module_name=module_name,
                strict=source == SOURCE_DTYPES,
            )
