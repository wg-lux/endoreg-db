from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, TypeAlias, cast

from django.apps import apps
from django.conf import settings
from lx_dtypes.models.interface.KnowledgeBaseResolver import (
    KnowledgeBaseRegistryError,
    KnowledgeBaseVersionNotFoundError,
    load_knowledge_base,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from lx_dtypes.models.knowledge_base.report_template import (
        ClassificationValidator as DtypesClassificationValidator,
        ExaminationValidator as DtypesExaminationValidator,
        FindingsValidator as DtypesFindingsValidator,
        ReportFinding as DtypesReportFinding,
        ReportTemplate as DtypesReportTemplate,
        ReportTemplateSection as DtypesReportTemplateSection,
    )
    from lx_dtypes.models.knowledge_base.classification import (
        Classification as DtypesClassification,
    )
    from lx_dtypes.models.knowledge_base.classification_choice import (
        ClassificationChoice as DtypesClassificationChoice,
    )
    from lx_dtypes.models.knowledge_base.classification_choice_descriptor import (
        ClassificationChoiceDescriptor as DtypesClassificationChoiceDescriptor,
    )
else:
    DtypesClassificationValidator = Any
    DtypesExaminationValidator = Any
    DtypesFindingsValidator = Any
    DtypesReportFinding = Any
    DtypesReportTemplate = Any
    DtypesReportTemplateSection = Any
    DtypesClassification = Any
    DtypesClassificationChoice = Any
    DtypesClassificationChoiceDescriptor = Any

ClassificationValidatorLike: TypeAlias = (
    DtypesClassificationValidator | Mapping[str, Any]
)
FindingsValidatorLike: TypeAlias = DtypesFindingsValidator | Mapping[str, Any]
ExaminationValidatorLike: TypeAlias = DtypesExaminationValidator | Mapping[str, Any]
ReportTemplateLike: TypeAlias = DtypesReportTemplate | Mapping[str, Any]
ReportTemplateSectionLike: TypeAlias = DtypesReportTemplateSection | Mapping[str, Any]
ReportFindingLike: TypeAlias = DtypesReportFinding | Mapping[str, Any]
ClassificationLike: TypeAlias = DtypesClassification | Mapping[str, Any]
ClassificationChoiceLike: TypeAlias = DtypesClassificationChoice | Mapping[str, Any]
ClassificationChoiceDescriptorLike: TypeAlias = (
    DtypesClassificationChoiceDescriptor | Mapping[str, Any]
)

LOOKUP_REQUIREMENT_SOURCE_LEGACY_DB = "legacy_db"
LOOKUP_REQUIREMENT_SOURCE_DTYPES = "dtypes"
LOOKUP_REQUIREMENT_SOURCE_HYBRID_COMPARE = "hybrid_compare"
LOOKUP_REQUIREMENT_SOURCE_VALUES = {
    LOOKUP_REQUIREMENT_SOURCE_LEGACY_DB,
    LOOKUP_REQUIREMENT_SOURCE_DTYPES,
    LOOKUP_REQUIREMENT_SOURCE_HYBRID_COMPARE,
}

DEFAULT_LOOKUP_REQUIREMENT_SOURCE = LOOKUP_REQUIREMENT_SOURCE_DTYPES
DEFAULT_LOOKUP_DTYPES_MODULE = "report_template_examples"
DEFAULT_LOOKUP_DTYPES_MODULE_VERSION = ""
DEFAULT_LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED = False
LOOKUP_ID_MAX = 2_147_483_647


class DtypesRequirementEvaluationError(RuntimeError):
    pass


class DtypesKnowledgeBaseResolutionError(DtypesRequirementEvaluationError):
    pass


class RelatedManagerLike(Protocol):
    def all(self) -> Sequence[Any]: ...


class PatientExaminationLike(Protocol):
    id: int
    examination: Any
    patient_findings: RelatedManagerLike
    knowledge_base_module: str | None
    knowledge_base_version: str | None


class DtypesKnowledgeBaseLike(Protocol):
    report_template: Mapping[str, ReportTemplateLike]
    report_template_section: Mapping[str, ReportTemplateSectionLike]
    report_finding: Mapping[str, ReportFindingLike]
    classification_validator: Mapping[str, ClassificationValidatorLike]
    findings_validator: Mapping[str, FindingsValidatorLike]
    examination_validator: Mapping[str, ExaminationValidatorLike]
    classification: Mapping[str, ClassificationLike]
    classification_choice: Mapping[str, ClassificationChoiceLike]
    classification_choice_descriptor: Mapping[str, ClassificationChoiceDescriptorLike]


def get_lookup_requirement_source() -> str:
    """
    Return the configured requirement source mode with safe fallback.
    """
    raw_value = (
        str(
            getattr(
                settings,
                "LOOKUP_REQUIREMENT_SOURCE",
                DEFAULT_LOOKUP_REQUIREMENT_SOURCE,
            )
        )
        .strip()
        .lower()
    )
    if raw_value in LOOKUP_REQUIREMENT_SOURCE_VALUES:
        return raw_value
    return DEFAULT_LOOKUP_REQUIREMENT_SOURCE


def get_lookup_dtypes_module_name() -> str:
    """
    Return the configured dtypes module name used for requirement validators.
    """
    raw_value = str(
        getattr(settings, "LOOKUP_DTYPES_MODULE_NAME", DEFAULT_LOOKUP_DTYPES_MODULE)
    ).strip()
    return raw_value or DEFAULT_LOOKUP_DTYPES_MODULE


def get_lookup_dtypes_module_version() -> str | None:
    """
    Return the configured default dtypes module version, if pinned.
    """
    raw_value = str(
        getattr(
            settings,
            "LOOKUP_DTYPES_MODULE_VERSION",
            DEFAULT_LOOKUP_DTYPES_MODULE_VERSION,
        )
    ).strip()
    return raw_value or None


def get_lookup_requirement_legacy_fallback_enabled() -> bool:
    """
    Return whether emergency fallback to legacy requirement runtime is enabled.
    """
    return bool(
        getattr(
            settings,
            "LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED",
            DEFAULT_LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED,
        )
    )


def _resolve_configured_dtypes_data_root() -> Path | None:
    configured_root = str(getattr(settings, "LOOKUP_DTYPES_DATA_ROOT", "")).strip()
    if not configured_root:
        return None

    configured_path = Path(configured_root).expanduser().resolve()
    if configured_path.exists():
        return configured_path

    logger.warning(
        "dtypes requirement service: configured LOOKUP_DTYPES_DATA_ROOT does not exist: %s",
        configured_path,
    )
    return None


@lru_cache(maxsize=32)
def _load_dtypes_kb(
    module_name: str,
    version: str | None = None,
) -> DtypesKnowledgeBaseLike | None:
    try:
        configured_data_root = _resolve_configured_dtypes_data_root()
        return cast(
            DtypesKnowledgeBaseLike,
            load_knowledge_base(
                module_name,
                version=version,
                input_dirs=[configured_data_root]
                if version is None and configured_data_root is not None
                else None,
            ),
        )
    except KnowledgeBaseVersionNotFoundError:
        raise
    except KnowledgeBaseRegistryError:
        raise
    except Exception as exc:
        logger.debug(
            "dtypes requirement service: failed to load module '%s' version '%s': %s",
            module_name,
            version,
            exc,
        )
        return None


def _obj_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_requested_kb_identity(
    pe: PatientExaminationLike,
) -> tuple[str, str | None]:
    persisted_module_name = _as_str(_obj_get(pe, "knowledge_base_module"))
    persisted_version = _as_str(_obj_get(pe, "knowledge_base_version"))
    module_name = persisted_module_name or get_lookup_dtypes_module_name()
    version = persisted_version or get_lookup_dtypes_module_version()
    return module_name, version


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out: list[str] = []
        for item in value:
            text = _as_str(item)
            if text:
                out.append(text)
        return out
    single_item = _as_str(value)
    return [single_item] if single_item else []


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value))
    except Exception:
        return None


def _stable_lookup_id_for_name(name: str) -> int:
    digest = hashlib.blake2s(name.encode("utf-8"), digest_size=4).digest()
    value = int.from_bytes(digest, byteorder="big") & 0x7FFFFFFF
    return value if value > 0 else 1


def _assign_stable_lookup_ids(names: Sequence[str]) -> dict[str, int]:
    """
    Assign collision-safe numeric IDs deterministically.

    IDs are stable for a given set of names regardless of evaluation order.
    """
    assigned: dict[str, int] = {}
    used_ids: dict[int, str] = {}

    for name in sorted({item for item in names if item}):
        probe = 0
        while True:
            candidate_seed = f"{name}#{probe}" if probe else name
            candidate = _stable_lookup_id_for_name(candidate_seed)
            if candidate not in used_ids:
                used_ids[candidate] = name
                assigned[name] = candidate
                break
            probe += 1
            if probe > LOOKUP_ID_MAX:
                raise DtypesRequirementEvaluationError(
                    "failed to allocate stable lookup IDs due to excessive collisions"
                )
    return assigned


def _extract_classification_values(patient_finding_classification: Any) -> list[Any]:
    values: list[Any] = []

    classification_choice = _obj_get(
        patient_finding_classification, "classification_choice"
    )
    choice_name = _as_str(_obj_get(classification_choice, "name"))
    if choice_name:
        values.append(choice_name)
        numeric = _coerce_float(choice_name)
        if numeric is not None:
            values.append(numeric)

    for payload in (
        _obj_get(patient_finding_classification, "subcategories", {}),
        _obj_get(patient_finding_classification, "numerical_descriptors", {}),
    ):
        if not isinstance(payload, Mapping):
            continue
        for raw in payload.values():
            if isinstance(raw, Mapping):
                raw_value = raw.get("value")
            else:
                raw_value = raw
            if raw_value in (None, ""):
                continue
            values.append(raw_value)
            numeric = _coerce_float(raw_value)
            if numeric is not None:
                values.append(numeric)

    return values


def _collect_patient_findings(
    pe: PatientExaminationLike,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, int]]:
    observations_by_finding: dict[str, list[dict[str, Any]]] = {}
    finding_ids_by_name: dict[str, int] = {}
    classification_ids_by_name: dict[str, int] = {}

    manager = _obj_get(pe, "patient_findings")
    if manager is None:
        raise DtypesRequirementEvaluationError(
            f"patient_findings relation is unavailable for patient_examination={pe.id}"
        )

    try:
        patient_findings = list(manager.all())
    except Exception as exc:
        raise DtypesRequirementEvaluationError(
            f"failed loading patient_findings for patient_examination={pe.id}: {exc}"
        ) from exc

    for patient_finding in patient_findings:
        if _obj_get(patient_finding, "is_active", True) is False:
            continue

        finding = _obj_get(patient_finding, "finding")
        finding_name = _as_str(_obj_get(finding, "name"))
        if not finding_name:
            continue

        finding_id = _obj_get(finding, "id")
        if isinstance(finding_id, int):
            finding_ids_by_name[finding_name] = finding_id

        classifications_by_name: dict[str, list[Any]] = {}
        intervention_names: list[str] = []
        classification_manager = _obj_get(patient_finding, "classifications")
        classification_rows: list[Any] = []
        if classification_manager is not None:
            try:
                classification_rows = list(classification_manager.all())
            except Exception as exc:
                raise DtypesRequirementEvaluationError(
                    "failed loading patient finding classifications "
                    f"(patient_examination={pe.id}, finding={finding_name}): {exc}"
                ) from exc

        for row in classification_rows:
            if _obj_get(row, "is_active", True) is False:
                continue
            classification = _obj_get(row, "classification")
            classification_name = _as_str(_obj_get(classification, "name"))
            if not classification_name:
                continue
            classification_id = _obj_get(classification, "id")
            if isinstance(classification_id, int):
                classification_ids_by_name[classification_name] = classification_id
            classifications_by_name.setdefault(classification_name, []).extend(
                _extract_classification_values(row)
            )

        intervention_manager = _obj_get(patient_finding, "interventions")
        if intervention_manager is not None:
            try:
                intervention_rows = list(intervention_manager.all())
            except Exception as exc:
                raise DtypesRequirementEvaluationError(
                    "failed loading patient finding interventions "
                    f"(patient_examination={pe.id}, finding={finding_name}): {exc}"
                ) from exc
            for row in intervention_rows:
                if _obj_get(row, "is_active", True) is False:
                    continue
                intervention_name = _as_str(
                    _obj_get(_obj_get(row, "intervention"), "name")
                )
                if intervention_name:
                    intervention_names.append(intervention_name)

        observations_by_finding.setdefault(finding_name, []).append(
            {
                "classifications": classifications_by_name,
                "interventions": sorted(set(intervention_names)),
            }
        )

    return observations_by_finding, finding_ids_by_name, classification_ids_by_name


def _reported_findings_payload(
    observations_by_finding: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for finding_name, observations in observations_by_finding.items():
        for observation in observations:
            payload.append(
                {
                    "finding": finding_name,
                    "classifications": observation.get("classifications", {}),
                    "interventions": observation.get("interventions", []),
                }
            )
    return payload


def _merge_available_finding_ids(pe: Any, finding_ids_by_name: dict[str, int]) -> None:
    examination = _obj_get(pe, "examination")
    get_available_findings = _obj_get(examination, "get_available_findings")
    if not callable(get_available_findings):
        return
    try:
        available_findings = list(get_available_findings())
    except Exception as exc:
        logger.warning(
            "dtypes requirement service: failed loading examination available findings "
            "(patient_examination=%s): %s",
            _obj_get(pe, "id"),
            exc,
        )
        return
    for finding in available_findings:
        finding_name = _as_str(_obj_get(finding, "name"))
        finding_id = _obj_get(finding, "id")
        if finding_name and isinstance(finding_id, int):
            finding_ids_by_name[finding_name] = finding_id


def _fill_missing_finding_ids(
    finding_names: set[str], finding_ids_by_name: dict[str, int]
) -> None:
    missing_names = [name for name in finding_names if name not in finding_ids_by_name]
    if not missing_names:
        return
    try:
        Finding = apps.get_model("endoreg_db", "Finding")
        for finding in Finding.objects.filter(name__in=missing_names).only(
            "id", "name"
        ):
            finding_ids_by_name[finding.name] = finding.id
    except Exception as exc:
        logger.warning(
            "dtypes requirement service: failed backfilling finding IDs: %s",
            exc,
        )


def _fill_missing_classification_ids(
    classification_names: set[str],
    classification_ids_by_name: dict[str, int],
) -> None:
    missing_names = [
        name for name in classification_names if name not in classification_ids_by_name
    ]
    if not missing_names:
        return
    try:
        FindingClassification = apps.get_model("endoreg_db", "FindingClassification")
        for classification in FindingClassification.objects.filter(
            name__in=missing_names
        ).only("id", "name"):
            classification_ids_by_name[classification.name] = classification.id
    except Exception as exc:
        logger.warning(
            "dtypes requirement service: failed backfilling classification IDs: %s",
            exc,
        )


def _as_comparable_pair(left: Any, right: Any) -> tuple[Any, Any]:
    if left is None or right is None:
        return left, right
    left_number = _coerce_float(left)
    right_number = _coerce_float(right)
    if left_number is not None and right_number is not None:
        return left_number, right_number
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().lower(), right.strip().lower()
    return left, right


def _compare_value(left: Any, comparator: str, right: Any) -> bool:
    cmp_name = comparator.strip().lower()
    if cmp_name in ("eq", "=="):
        left_cmp, right_cmp = _as_comparable_pair(left, right)
        return left_cmp == right_cmp
    if cmp_name in ("ne", "!="):
        left_cmp, right_cmp = _as_comparable_pair(left, right)
        return left_cmp != right_cmp
    if cmp_name in ("gt", ">"):
        left_number = _coerce_float(left)
        right_number = _coerce_float(right)
        return (
            left_number is not None
            and right_number is not None
            and left_number > right_number
        )
    if cmp_name in ("gte", ">="):
        left_number = _coerce_float(left)
        right_number = _coerce_float(right)
        return (
            left_number is not None
            and right_number is not None
            and left_number >= right_number
        )
    if cmp_name in ("lt", "<"):
        left_number = _coerce_float(left)
        right_number = _coerce_float(right)
        return (
            left_number is not None
            and right_number is not None
            and left_number < right_number
        )
    if cmp_name in ("lte", "<="):
        left_number = _coerce_float(left)
        right_number = _coerce_float(right)
        return (
            left_number is not None
            and right_number is not None
            and left_number <= right_number
        )
    if cmp_name == "in":
        if isinstance(right, Sequence) and not isinstance(
            right, (str, bytes, bytearray)
        ):
            return any(_compare_value(left, "eq", item) for item in right)
        return _compare_value(left, "eq", right)
    if cmp_name in ("exists", "present"):
        return left is not None
    return _compare_value(left, "eq", right)


def _condition_rule_matches(
    observations_for_finding: list[dict[str, Any]],
    *,
    rule: Mapping[str, Any],
) -> bool:
    classification_name = _as_str(rule.get("classification"))
    if not classification_name:
        return False

    comparator = str(rule.get("comparator", "eq"))
    comparator_name = comparator.strip().lower()
    if comparator_name in ("exists", "present"):
        return any(
            classification_name in observation.get("classifications", {})
            for observation in observations_for_finding
        )

    expected_value = rule.get("value")
    values: list[Any] = []
    for observation in observations_for_finding:
        by_name = observation.get("classifications", {})
        if classification_name in by_name:
            values.extend(by_name.get(classification_name, []))

    if not values:
        return False
    return any(_compare_value(value, comparator, expected_value) for value in values)


def _evaluate_query_condition(
    observations_for_finding: list[dict[str, Any]],
    *,
    condition: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    any_rules_raw = condition.get("any")
    all_rules_raw = condition.get("all")
    any_rules = (
        [rule for rule in any_rules_raw if isinstance(rule, Mapping)]
        if isinstance(any_rules_raw, Sequence)
        else []
    )
    all_rules = (
        [rule for rule in all_rules_raw if isinstance(rule, Mapping)]
        if isinstance(all_rules_raw, Sequence)
        else []
    )

    if any_rules:
        any_match = any(
            _condition_rule_matches(observations_for_finding, rule=rule)
            for rule in any_rules
        )
    else:
        any_match = None

    if all_rules:
        all_match = all(
            _condition_rule_matches(observations_for_finding, rule=rule)
            for rule in all_rules
        )
    else:
        all_match = None

    if any_match is None and all_match is None:
        return True, []
    if any_match is not None and all_match is not None:
        trigger_matched = any_match and all_match
    elif any_match is not None:
        trigger_matched = any_match
    else:
        trigger_matched = bool(all_match)

    if not trigger_matched:
        return True, []

    then_requires_raw = condition.get("then_requires")
    then_requires = (
        [item for item in then_requires_raw if isinstance(item, Mapping)]
        if isinstance(then_requires_raw, Sequence)
        else []
    )
    missing_classifications: list[str] = []
    for required_item in then_requires:
        required_classification = _as_str(required_item.get("classification"))
        if not required_classification:
            continue
        required_present = any(
            required_classification in observation.get("classifications", {})
            for observation in observations_for_finding
        )
        if not required_present:
            missing_classifications.append(required_classification)

    return (not missing_classifications), missing_classifications


def _evaluate_findings_validator(
    *,
    findings_validator: FindingsValidatorLike,
    observations_by_finding: Mapping[str, list[dict[str, Any]]],
) -> tuple[bool, dict[str, Any]]:
    query = _as_mapping(_obj_get(findings_validator, "query", {}))

    finding_name = _as_str(query.get("finding")) or _as_str(
        _obj_get(findings_validator, "finding")
    )
    operator = (
        _as_str(query.get("operator"))
        or _as_str(_obj_get(findings_validator, "operator"))
        or "exists"
    ).lower()

    if not finding_name:
        return False, {
            "finding_name": None,
            "missing_finding": False,
            "missing_classifications": [],
            "reason": "validator_missing_finding_name",
        }

    observations_for_finding = list(observations_by_finding.get(finding_name, []))
    finding_exists = bool(observations_for_finding)

    if operator == "exists":
        return finding_exists, {
            "finding_name": finding_name,
            "missing_finding": not finding_exists,
            "missing_classifications": [],
            "reason": "validator_exists",
        }

    if operator == "missing":
        return (not finding_exists), {
            "finding_name": finding_name,
            "missing_finding": False,
            "missing_classifications": [],
            "reason": "validator_missing",
        }

    if operator == "condition":
        condition = _as_mapping(query.get("condition"))
        condition_ok, missing_classifications = _evaluate_query_condition(
            observations_for_finding,
            condition=condition,
        )
        return condition_ok, {
            "finding_name": finding_name,
            "missing_finding": False,
            "missing_classifications": missing_classifications,
            "reason": "validator_condition",
        }

    logger.debug(
        "dtypes requirement service: unsupported findings operator '%s' on '%s'",
        operator,
        finding_name,
    )
    return False, {
        "finding_name": finding_name,
        "missing_finding": False,
        "missing_classifications": [],
        "reason": "validator_unsupported_operator",
    }


def _normalize_report_finding_requirement(
    *,
    finding_ref: Any,
    report_findings_by_name: Mapping[str, ReportFindingLike],
) -> tuple[str | None, bool, list[str]]:
    if isinstance(finding_ref, str):
        report_finding = report_findings_by_name.get(finding_ref)
        if report_finding is None:
            return finding_ref, False, []
        finding_name = _as_str(_obj_get(report_finding, "finding"))
        required = bool(_obj_get(report_finding, "required", False))
        class_names: list[str] = []
        for classification in _obj_get(report_finding, "classifications", []) or []:
            if not isinstance(classification, Mapping) and not hasattr(
                classification, "classification"
            ):
                continue
            if not bool(_obj_get(classification, "required", False)):
                continue
            classification_name = _as_str(_obj_get(classification, "classification"))
            if classification_name:
                class_names.append(classification_name)
        return finding_name, required, sorted(set(class_names))

    finding_name = _as_str(_obj_get(finding_ref, "finding"))
    required = bool(_obj_get(finding_ref, "required", False))
    class_names_inline: list[str] = []
    classifications = _obj_get(finding_ref, "classifications", []) or []
    for classification in classifications:
        if not isinstance(classification, Mapping) and not hasattr(
            classification, "classification"
        ):
            continue
        if not bool(_obj_get(classification, "required", False)):
            continue
        classification_name = _as_str(_obj_get(classification, "classification"))
        if classification_name:
            class_names_inline.append(classification_name)
    return finding_name, required, sorted(set(class_names_inline))


def _collect_template_required_findings(
    *,
    template: ReportTemplateLike,
    sections_by_name: Mapping[str, ReportTemplateSectionLike],
    report_findings_by_name: Mapping[str, ReportFindingLike],
) -> dict[str, list[str]]:
    required_findings: dict[str, list[str]] = {}
    section_names = _as_str_list(_obj_get(template, "report_sections", []))
    for section_name in section_names:
        section = sections_by_name.get(section_name)
        if section is None:
            continue
        for finding_ref in _obj_get(section, "findings", []) or []:
            finding_name, required, required_classifications = (
                _normalize_report_finding_requirement(
                    finding_ref=finding_ref,
                    report_findings_by_name=report_findings_by_name,
                )
            )
            if not finding_name or not required:
                continue
            merged = set(required_findings.get(finding_name, []))
            merged.update(required_classifications)
            required_findings[finding_name] = sorted(merged)
    return required_findings


def _collect_template_finding_classification_specs(
    *,
    template: ReportTemplateLike,
    sections_by_name: Mapping[str, ReportTemplateSectionLike],
    report_findings_by_name: Mapping[str, ReportFindingLike],
) -> dict[str, dict[str, str]]:
    specs: dict[str, dict[str, str]] = {}
    section_names = _as_str_list(_obj_get(template, "report_sections", []))
    for section_name in section_names:
        section = sections_by_name.get(section_name)
        if section is None:
            continue
        for finding_ref in _obj_get(section, "findings", []) or []:
            finding_name, _, _ = _normalize_report_finding_requirement(
                finding_ref=finding_ref,
                report_findings_by_name=report_findings_by_name,
            )
            if not finding_name:
                continue

            if isinstance(finding_ref, str):
                source_requirement = report_findings_by_name.get(finding_ref)
                if source_requirement is None:
                    continue
            else:
                source_requirement = finding_ref

            class_specs = specs.setdefault(finding_name, {})
            for classification in (
                _obj_get(source_requirement, "classifications", []) or []
            ):
                if not isinstance(classification, Mapping) and not hasattr(
                    classification, "classification"
                ):
                    continue
                classification_name = _as_str(
                    _obj_get(classification, "classification")
                )
                if not classification_name:
                    continue
                precedence = (
                    "required"
                    if bool(_obj_get(classification, "required", False))
                    else "optional"
                )
                existing = class_specs.get(classification_name)
                if existing == "required":
                    continue
                class_specs[classification_name] = precedence

    return {
        finding_name: dict(sorted(class_specs.items()))
        for finding_name, class_specs in specs.items()
    }


def _classification_exists_on_observations(
    *,
    observations_for_finding: Sequence[Mapping[str, Any]],
    classification_name: str,
) -> bool:
    return any(
        classification_name in (observation.get("classifications") or {})
        for observation in observations_for_finding
    )


def _classification_data_type_hint(
    *,
    classification: ClassificationLike | None,
    classification_choices_by_name: Mapping[str, ClassificationChoiceLike],
    classification_choice_descriptors_by_name: Mapping[
        str, ClassificationChoiceDescriptorLike
    ],
) -> tuple[str, list[str], list[str], bool]:
    if classification is None:
        return "unknown", [], [], False

    choice_names = _as_str_list(_obj_get(classification, "classification_choices", []))
    descriptor_types: set[str] = set()
    allows_multiple = False

    for choice_name in choice_names:
        choice = classification_choices_by_name.get(choice_name)
        if choice is None:
            continue
        descriptor_names = _as_str_list(
            _obj_get(choice, "classification_choice_descriptors", [])
        )
        for descriptor_name in descriptor_names:
            descriptor = classification_choice_descriptors_by_name.get(descriptor_name)
            if descriptor is None:
                continue
            descriptor_type = _as_str(
                _obj_get(descriptor, "classification_choice_descriptor_type")
            )
            if descriptor_type:
                descriptor_types.add(descriptor_type)
            allows_multiple = allows_multiple or bool(
                _obj_get(descriptor, "selection_multiple", False)
            )

    if "boolean" in descriptor_types:
        return "binary", choice_names, sorted(descriptor_types), allows_multiple
    if descriptor_types.intersection({"numeric", "text"}):
        return (
            "non_categorical",
            choice_names,
            sorted(descriptor_types),
            allows_multiple,
        )
    if len(choice_names) == 2:
        return "binary", choice_names, sorted(descriptor_types), allows_multiple
    if choice_names:
        return "ordered", choice_names, sorted(descriptor_types), allows_multiple
    return "non_categorical", choice_names, sorted(descriptor_types), allows_multiple


def _build_classification_hint(
    *,
    kb: DtypesKnowledgeBaseLike | Mapping[str, Any],
    classification_name: str,
    precedence: str,
    classification_ids_by_name: Mapping[str, int],
) -> dict[str, Any]:
    classifications_by_name = _obj_get(kb, "classification", {}) or {}
    if not isinstance(classifications_by_name, Mapping):
        classifications_by_name = {}
    classification_choices_by_name = _obj_get(kb, "classification_choice", {}) or {}
    if not isinstance(classification_choices_by_name, Mapping):
        classification_choices_by_name = {}
    descriptors_by_name = _obj_get(kb, "classification_choice_descriptor", {}) or {}
    if not isinstance(descriptors_by_name, Mapping):
        descriptors_by_name = {}

    classification = classifications_by_name.get(classification_name)
    data_type_hint, choice_names, descriptor_types, allows_multiple = (
        _classification_data_type_hint(
            classification=classification,
            classification_choices_by_name=classification_choices_by_name,
            classification_choice_descriptors_by_name=descriptors_by_name,
        )
    )

    hint: dict[str, Any] = {
        "classification_name": classification_name,
        "precedence": precedence,
        "data_type_hint": data_type_hint,
    }
    classification_id = classification_ids_by_name.get(classification_name)
    if classification_id is not None:
        hint["classification_id"] = classification_id
    if choice_names:
        hint["choice_names"] = choice_names
    if descriptor_types:
        hint["descriptor_types"] = descriptor_types
    if allows_multiple:
        hint["allows_multiple"] = True
    return hint


def _matching_templates_for_examination(
    kb: DtypesKnowledgeBaseLike | Mapping[str, Any],
    examination_name: str,
) -> list[ReportTemplateLike]:
    templates_by_name = _obj_get(kb, "report_template", {}) or {}
    if not isinstance(templates_by_name, Mapping):
        return []
    out: list[ReportTemplateLike] = []
    for template in templates_by_name.values():
        template_examination_name = _as_str(_obj_get(template, "examination"))
        if template_examination_name and template_examination_name == examination_name:
            out.append(template)
    return sorted(out, key=lambda template: _as_str(_obj_get(template, "name")) or "")


def _collect_template_validator_names(
    *,
    template: ReportTemplateLike,
    examination_validators_by_name: Mapping[str, ExaminationValidatorLike],
) -> tuple[list[str], list[str]]:
    validators = _obj_get(template, "validators", {})
    if validators is None:
        validators = {}
    top_level_finding_validators = _as_str_list(
        _obj_get(validators, "findings_validators", [])
    )
    top_level_exam_validators = _as_str_list(
        _obj_get(validators, "examination_validators", [])
    )

    finding_validators: list[str] = list(top_level_finding_validators)
    finding_seen = set(top_level_finding_validators)

    exam_validators: list[str] = []
    exam_seen: set[str] = set()
    stack = list(top_level_exam_validators)
    while stack:
        current = stack.pop(0)
        if current in exam_seen:
            continue
        exam_seen.add(current)
        exam_validators.append(current)
        validator = examination_validators_by_name.get(current)
        if validator is None:
            continue
        for nested_finding in _as_str_list(
            _obj_get(validator, "finding_validators", [])
        ):
            if nested_finding in finding_seen:
                continue
            finding_seen.add(nested_finding)
            finding_validators.append(nested_finding)
        for nested_exam in _as_str_list(
            _obj_get(validator, "examination_validators", [])
        ):
            if nested_exam in exam_seen:
                continue
            stack.append(nested_exam)

    return finding_validators, exam_validators


def _build_dtypes_requirement_guidance(
    *,
    pe: PatientExaminationLike,
    kb: DtypesKnowledgeBaseLike | Mapping[str, Any],
    selected_requirement_set_ids: list[int] | None,
) -> dict[str, Any] | None:
    examination_name = _as_str(_obj_get(_obj_get(pe, "examination"), "name"))
    if not examination_name:
        return None

    templates = _matching_templates_for_examination(kb, examination_name)
    if not templates:
        logger.debug(
            "dtypes requirement service: no templates for examination '%s'",
            examination_name,
        )
        return None

    template_name_to_template: dict[str, ReportTemplateLike] = {}
    for template in templates:
        template_name = _as_str(_obj_get(template, "name"))
        if not template_name:
            continue
        template_name_to_template[template_name] = template

    set_lookup_names = [
        f"template:{template_name}" for template_name in template_name_to_template
    ]
    set_id_by_lookup_name = _assign_stable_lookup_ids(set_lookup_names)
    set_ids_to_templates: dict[int, ReportTemplateLike] = {
        set_id_by_lookup_name[f"template:{template_name}"]: template
        for template_name, template in template_name_to_template.items()
    }

    if not set_ids_to_templates:
        return None

    requested_set_ids = list(selected_requirement_set_ids or [])
    requested_known_ids = [
        set_id for set_id in requested_set_ids if set_id in set_ids_to_templates
    ]
    active_set_ids = requested_known_ids or sorted(set_ids_to_templates.keys())

    report_findings_by_name: Mapping[str, ReportFindingLike] = (
        _obj_get(kb, "report_finding", {}) or {}
    )
    if not isinstance(report_findings_by_name, Mapping):
        report_findings_by_name = {}
    sections_by_name: Mapping[str, ReportTemplateSectionLike] = (
        _obj_get(kb, "report_template_section", {}) or {}
    )
    if not isinstance(sections_by_name, Mapping):
        sections_by_name = {}
    findings_validators_by_name: Mapping[str, FindingsValidatorLike] = (
        _obj_get(kb, "findings_validator", {}) or {}
    )
    if not isinstance(findings_validators_by_name, Mapping):
        findings_validators_by_name = {}
    classification_validators_by_name: Mapping[str, ClassificationValidatorLike] = (
        _obj_get(kb, "classification_validator", {}) or {}
    )
    if not isinstance(classification_validators_by_name, Mapping):
        classification_validators_by_name = {}
    intervention_validators_by_name: Mapping[str, Mapping[str, Any]] = (
        _obj_get(kb, "intervention_validator", {}) or {}
    )
    if not isinstance(intervention_validators_by_name, Mapping):
        intervention_validators_by_name = {}
    unit_validators_by_name: Mapping[str, Mapping[str, Any]] = (
        _obj_get(kb, "unit_validator", {}) or {}
    )
    if not isinstance(unit_validators_by_name, Mapping):
        unit_validators_by_name = {}
    examination_validators_by_name: Mapping[str, ExaminationValidatorLike] = (
        _obj_get(kb, "examination_validator", {}) or {}
    )
    if not isinstance(examination_validators_by_name, Mapping):
        examination_validators_by_name = {}

    (
        observations_by_finding,
        finding_ids_by_name,
        classification_ids_by_name,
    ) = _collect_patient_findings(pe)
    _merge_available_finding_ids(pe, finding_ids_by_name)

    referenced_finding_names: set[str] = set(observations_by_finding.keys())
    referenced_classification_names: set[str] = set(classification_ids_by_name.keys())

    template_required_findings: dict[int, dict[str, list[str]]] = {}
    template_finding_classification_specs: dict[int, dict[str, dict[str, str]]] = {}
    validators_per_set: dict[int, tuple[list[str], list[str]]] = {}
    for set_id in active_set_ids:
        template = set_ids_to_templates[set_id]
        required_findings = _collect_template_required_findings(
            template=template,
            sections_by_name=sections_by_name,
            report_findings_by_name=report_findings_by_name,
        )
        template_required_findings[set_id] = required_findings
        for finding_name, class_names in required_findings.items():
            referenced_finding_names.add(finding_name)
            referenced_classification_names.update(class_names)
        finding_classification_specs = _collect_template_finding_classification_specs(
            template=template,
            sections_by_name=sections_by_name,
            report_findings_by_name=report_findings_by_name,
        )
        template_finding_classification_specs[set_id] = finding_classification_specs
        for finding_name, class_specs in finding_classification_specs.items():
            referenced_finding_names.add(finding_name)
            referenced_classification_names.update(class_specs.keys())

        finding_validator_names, exam_validator_names = (
            _collect_template_validator_names(
                template=template,
                examination_validators_by_name=examination_validators_by_name,
            )
        )
        validators_per_set[set_id] = (finding_validator_names, exam_validator_names)
        for classification_validator_name in _as_str_list(
            _obj_get(
                _obj_get(template, "validators", {}), "classification_validators", []
            )
        ):
            validator = classification_validators_by_name.get(
                classification_validator_name
            )
            if validator is None:
                continue
            validator_finding_name = _as_str(_obj_get(validator, "finding")) or _as_str(
                _obj_get(_obj_get(validator, "query", {}), "finding")
            )
            validator_classification_name = _as_str(
                _obj_get(validator, "classification")
            ) or _as_str(_obj_get(_obj_get(validator, "query", {}), "classification"))
            if validator_finding_name:
                referenced_finding_names.add(validator_finding_name)
            if validator_classification_name:
                referenced_classification_names.add(validator_classification_name)
            condition = _as_mapping(
                _obj_get(_obj_get(validator, "query", {}), "condition", {})
            )
            if condition:
                for key in ("any", "all"):
                    rules = condition.get(key, [])
                    if not isinstance(rules, Sequence):
                        continue
                    for rule in rules:
                        if not isinstance(rule, Mapping):
                            continue
                        class_name = _as_str(rule.get("classification"))
                        if class_name:
                            referenced_classification_names.add(class_name)
        for finding_validator_name in finding_validator_names:
            validator = findings_validators_by_name.get(finding_validator_name)
            if validator is None:
                continue
            query = _as_mapping(_obj_get(validator, "query", {}))
            validator_finding_name = _as_str(_obj_get(validator, "finding")) or _as_str(
                _obj_get(query, "finding")
            )
            if validator_finding_name:
                referenced_finding_names.add(validator_finding_name)
            condition = _as_mapping(_obj_get(query, "condition", {}))
            if condition:
                for key in ("any", "all", "then_requires"):
                    rules = condition.get(key, [])
                    if not isinstance(rules, Sequence):
                        continue
                    for rule in rules:
                        if not isinstance(rule, Mapping):
                            continue
                        class_name = _as_str(rule.get("classification"))
                        if class_name:
                            referenced_classification_names.add(class_name)

    _fill_missing_finding_ids(referenced_finding_names, finding_ids_by_name)
    _fill_missing_classification_ids(
        referenced_classification_names, classification_ids_by_name
    )

    from lx_dtypes.models.knowledge_base.report_template import (
        ReportTemplate as RuntimeReportTemplate,
        evaluate_classification_validator_runtime,
        evaluate_findings_validator_runtime,
        evaluate_intervention_validator_runtime,
        evaluate_report_template_validators_runtime,
        evaluate_unit_validator_runtime,
    )

    reported_findings_payload = _reported_findings_payload(observations_by_finding)

    classifications_by_name: Mapping[str, ClassificationLike] = (
        _obj_get(kb, "classification", {}) or {}
    )
    if not isinstance(classifications_by_name, Mapping):
        classifications_by_name = {}
    classification_choices_by_name: Mapping[str, ClassificationChoiceLike] = (
        _obj_get(kb, "classification_choice", {}) or {}
    )
    if not isinstance(classification_choices_by_name, Mapping):
        classification_choices_by_name = {}
    descriptors_by_name: Mapping[str, ClassificationChoiceDescriptorLike] = (
        _obj_get(kb, "classification_choice_descriptor", {}) or {}
    )
    if not isinstance(descriptors_by_name, Mapping):
        descriptors_by_name = {}

    classification_validators_per_set: dict[
        int, Mapping[str, ClassificationValidatorLike]
    ] = {}
    classification_validator_names_per_set: dict[int, list[str]] = {}
    for set_id in active_set_ids:
        template = set_ids_to_templates[set_id]
        template_name = _as_str(_obj_get(template, "name"))
        if not template_name:
            continue
        resolver = getattr(kb, "get_report_template_classification_validators", None)
        if callable(resolver):
            resolved_validators = resolver(template_name)
        else:
            raw_validators = _obj_get(kb, "classification_validator", {}) or {}
            resolved_validators = (
                raw_validators if isinstance(raw_validators, Mapping) else {}
            )
        classification_validators_per_set[set_id] = resolved_validators
        classification_validator_names = _as_str_list(
            _obj_get(
                _obj_get(template, "validators", {}), "classification_validators", []
            )
        )
        classification_validator_names.extend(
            [
                validator_name
                for validator_name in resolved_validators.keys()
                if validator_name not in classification_validator_names
            ]
        )
        classification_validator_names_per_set[set_id] = classification_validator_names

    finding_runtime_cache: dict[str, dict[str, Any]] = {}
    classification_runtime_cache: dict[tuple[int, str], dict[str, Any]] = {}
    intervention_runtime_cache: dict[str, dict[str, Any]] = {}
    exam_runtime_cache: dict[tuple[int, str], dict[str, Any]] = {}
    unit_runtime_cache: dict[str, dict[str, Any]] = {}

    def evaluate_finding_validator_by_name(name: str) -> dict[str, Any]:
        if name in finding_runtime_cache:
            return finding_runtime_cache[name]
        validator = findings_validators_by_name.get(name)
        if validator is None:
            result = {
                "name": name,
                "ok": False,
                "finding": "unknown",
                "issues": [{"code": "unknown_findings_validator_reference"}],
                "missing_required_classifications": [],
            }
        else:
            result = evaluate_findings_validator_runtime(
                validator,
                reported_findings=reported_findings_payload,
            )
        finding_runtime_cache[name] = result
        return result

    def evaluate_classification_validator_by_name(
        set_id: int, name: str
    ) -> dict[str, Any]:
        cache_key = (set_id, name)
        if cache_key in classification_runtime_cache:
            return classification_runtime_cache[cache_key]
        validator = classification_validators_per_set.get(set_id, {}).get(name)
        if validator is None:
            result = {
                "name": name,
                "ok": False,
                "finding": "unknown",
                "classification": "unknown",
                "hint": {
                    "classification_name": "unknown",
                    "precedence": "required",
                    "data_type_hint": "unknown",
                },
                "issues": [{"code": "unknown_classification_validator_reference"}],
            }
        else:
            result = evaluate_classification_validator_runtime(
                validator,
                classifications=classifications_by_name,
                classification_choices=classification_choices_by_name,
                classification_choice_descriptors=descriptors_by_name,
                reported_findings=reported_findings_payload,
            )
        classification_runtime_cache[cache_key] = result
        return result

    def evaluate_exam_validator_by_name(set_id: int, name: str) -> dict[str, Any]:
        cache_key = (set_id, name)
        if cache_key in exam_runtime_cache:
            return exam_runtime_cache[cache_key]

        template = RuntimeReportTemplate.model_validate(
            {
                "name": f"runtime_exam_validator__{name}",
                "examination": examination_name,
                "report_sections": [],
                "validators": {
                    "findings_validators": [],
                    "classification_validators": [],
                    "examination_validators": [name],
                },
            }
        )
        result = evaluate_report_template_validators_runtime(
            template,
            classification_validators=classification_validators_per_set.get(set_id, {}),
            classification_validator_names=[],
            intervention_validators={},
            unit_validators={},
            findings_validators=findings_validators_by_name,
            examination_validators=examination_validators_by_name,
            classifications=classifications_by_name,
            classification_choices=classification_choices_by_name,
            classification_choice_descriptors=descriptors_by_name,
            interventions={},
            units={},
            reported_findings=reported_findings_payload,
        )["examination_validators"][0]
        exam_runtime_cache[cache_key] = result
        return result

    def evaluate_intervention_validator_by_name(name: str) -> dict[str, Any]:
        if name in intervention_runtime_cache:
            return intervention_runtime_cache[name]
        validator = intervention_validators_by_name.get(name)
        if validator is None:
            result = {
                "name": name,
                "ok": False,
                "finding": "unknown",
                "intervention": "unknown",
                "hint": {"intervention_name": "unknown", "precedence": "required"},
                "issues": [{"code": "unknown_intervention_validator_reference"}],
            }
        else:
            result = evaluate_intervention_validator_runtime(
                validator,
                interventions=_obj_get(kb, "intervention", {}) or {},
                reported_findings=reported_findings_payload,
            )
        intervention_runtime_cache[name] = result
        return result

    def evaluate_unit_validator_by_name(name: str) -> dict[str, Any]:
        if name in unit_runtime_cache:
            return unit_runtime_cache[name]
        validator = unit_validators_by_name.get(name)
        if validator is None:
            result = {
                "name": name,
                "ok": False,
                "finding": "unknown",
                "classification": "unknown",
                "unit": "unknown",
                "hint": {"unit_name": "unknown", "precedence": "required"},
                "issues": [{"code": "unknown_unit_validator_reference"}],
            }
        else:
            result = evaluate_unit_validator_runtime(
                validator,
                units=_obj_get(kb, "unit", {}) or {},
                reported_findings=reported_findings_payload,
            )
        unit_runtime_cache[name] = result
        return result

    requirements_by_set: dict[str, list[dict[str, Any]]] = {}
    requirement_status: dict[str, bool] = {}
    requirement_set_status: dict[str, bool] = {}
    requirement_defaults: dict[str, Any] = {}
    classification_choices: dict[str, Any] = {}
    suggested_actions: dict[str, list[dict[str, Any]]] = {}

    requirement_lookup_names: list[str] = []
    for set_id in active_set_ids:
        template = set_ids_to_templates[set_id]
        template_name = _as_str(_obj_get(template, "name")) or f"template_{set_id}"
        finding_validator_names, exam_validator_names = validators_per_set.get(
            set_id, ([], [])
        )
        classification_validator_names = sorted(
            classification_validator_names_per_set.get(set_id, [])
        )
        intervention_validator_names = _as_str_list(
            _obj_get(
                _obj_get(template, "validators", {}), "intervention_validators", []
            )
        )
        unit_validator_names = _as_str_list(
            _obj_get(_obj_get(template, "validators", {}), "unit_validators", [])
        )
        requirement_lookup_names.extend(
            [
                f"{template_name}:findings_validator:{validator_name}"
                for validator_name in finding_validator_names
            ]
        )
        requirement_lookup_names.extend(
            [
                f"{template_name}:classification_validator:{validator_name}"
                for validator_name in classification_validator_names
            ]
        )
        requirement_lookup_names.extend(
            [
                f"{template_name}:examination_validator:{validator_name}"
                for validator_name in exam_validator_names
            ]
        )
        requirement_lookup_names.extend(
            [
                f"{template_name}:intervention_validator:{validator_name}"
                for validator_name in intervention_validator_names
            ]
        )
        requirement_lookup_names.extend(
            [
                f"{template_name}:unit_validator:{validator_name}"
                for validator_name in unit_validator_names
            ]
        )
    requirement_id_by_lookup_name = _assign_stable_lookup_ids(requirement_lookup_names)

    for set_id in active_set_ids:
        template = set_ids_to_templates[set_id]
        template_name = _as_str(_obj_get(template, "name")) or f"template_{set_id}"
        required_findings = template_required_findings.get(set_id, {})
        finding_classification_specs = template_finding_classification_specs.get(
            set_id, {}
        )
        finding_validator_names, exam_validator_names = validators_per_set.get(
            set_id, ([], [])
        )
        classification_validator_names = sorted(
            classification_validator_names_per_set.get(set_id, [])
        )
        intervention_validator_names = _as_str_list(
            _obj_get(
                _obj_get(template, "validators", {}), "intervention_validators", []
            )
        )
        unit_validator_names = _as_str_list(
            _obj_get(_obj_get(template, "validators", {}), "unit_validators", [])
        )

        current_set_requirements: list[dict[str, Any]] = []
        current_set_statuses: list[bool] = []

        for validator_name in finding_validator_names:
            validator = findings_validators_by_name.get(validator_name)
            validator_finding_name = _as_str(_obj_get(validator, "finding")) or _as_str(
                _obj_get(_obj_get(validator, "query", {}), "finding")
            )
            req_lookup_name = f"{template_name}:findings_validator:{validator_name}"
            req_id = requirement_id_by_lookup_name[req_lookup_name]
            current_set_requirements.append(
                {"id": req_id, "name": f"findings_validator:{validator_name}"}
            )

            evaluation_result = evaluate_finding_validator_by_name(validator_name)
            ok = bool(evaluation_result.get("ok"))
            current_set_statuses.append(ok)
            requirement_status[str(req_id)] = bool(ok)

            default_finding_name = validator_finding_name
            required_class_names = (
                required_findings.get(default_finding_name, [])
                if default_finding_name
                else []
            )
            all_class_specs = (
                finding_classification_specs.get(default_finding_name, {})
                if default_finding_name
                else {}
            )
            all_class_hints = [
                _build_classification_hint(
                    kb=kb,
                    classification_name=class_name,
                    precedence=precedence,
                    classification_ids_by_name=classification_ids_by_name,
                )
                for class_name, precedence in all_class_specs.items()
            ]
            if default_finding_name in required_findings:
                default_payload: dict[str, Any] = {"finding_name": default_finding_name}
                finding_id = finding_ids_by_name.get(default_finding_name)
                if finding_id is not None:
                    default_payload["finding_id"] = finding_id
                class_ids = [
                    classification_ids_by_name[name]
                    for name in required_class_names
                    if name in classification_ids_by_name
                ]
                if class_ids:
                    default_payload["classification_ids"] = class_ids
                if required_class_names:
                    default_payload["classification_names"] = required_class_names
                if all_class_hints:
                    default_payload["classification_hints"] = all_class_hints
                    classification_choices[str(req_id)] = all_class_hints
                requirement_defaults[str(req_id)] = [default_payload]

            if ok:
                continue

            action_finding_name = (
                _as_str(evaluation_result.get("finding")) or default_finding_name
            )
            missing_classifications = [
                _as_str(item)
                for item in (
                    evaluation_result.get("missing_required_classifications") or []
                )
            ]
            missing_classification_names = [
                item for item in missing_classifications if item is not None
            ]
            finding_observations = (
                list(observations_by_finding.get(action_finding_name, []))
                if action_finding_name
                else []
            )
            action_type = (
                "add_classification"
                if finding_observations and missing_classification_names
                else "add_finding"
            )
            action: dict[str, Any] = {
                "type": action_type,
                "note": (
                    str((evaluation_result.get("issues") or [{}])[0].get("code"))
                    if evaluation_result.get("issues")
                    else "validator_unsatisfied"
                ),
                "classification_ids": [],
            }
            if action_finding_name:
                action["finding_name"] = action_finding_name
                finding_id = finding_ids_by_name.get(action_finding_name)
                if finding_id is not None:
                    action["finding_id"] = finding_id
            if action_type == "add_finding" and all_class_hints:
                action["classification_hints"] = all_class_hints
                if required_class_names:
                    action["classification_names"] = required_class_names
                    action["classification_ids"] = [
                        classification_ids_by_name[name]
                        for name in required_class_names
                        if name in classification_ids_by_name
                    ]
            if missing_classification_names:
                action["classification_names"] = missing_classification_names
                action["classification_ids"] = [
                    classification_ids_by_name[name]
                    for name in missing_classification_names
                    if name in classification_ids_by_name
                ]
                action["classification_hints"] = [
                    _build_classification_hint(
                        kb=kb,
                        classification_name=name,
                        precedence="required",
                        classification_ids_by_name=classification_ids_by_name,
                    )
                    for name in missing_classification_names
                ]
                classification_choices[str(req_id)] = action["classification_hints"]
            suggested_actions[str(req_id)] = [action]

        for validator_name in classification_validator_names:
            evaluation_result = evaluate_classification_validator_by_name(
                set_id, validator_name
            )
            finding_name = _as_str(evaluation_result.get("finding")) or "unknown"
            classification_name = (
                _as_str(evaluation_result.get("classification")) or "unknown"
            )
            req_lookup_name = (
                f"{template_name}:classification_validator:{validator_name}"
            )
            req_id = requirement_id_by_lookup_name[req_lookup_name]
            current_set_requirements.append(
                {"id": req_id, "name": f"classification_validator:{validator_name}"}
            )

            ok = bool(evaluation_result.get("ok"))
            current_set_statuses.append(ok)
            requirement_status[str(req_id)] = ok

            hint = dict(evaluation_result.get("hint") or {})
            classification_id = classification_ids_by_name.get(classification_name)
            if classification_id is not None:
                hint.setdefault("classification_id", classification_id)
            classification_choices[str(req_id)] = [hint]

            classification_default_payload: dict[str, Any] = {
                "finding_name": finding_name,
                "classification_name": classification_name,
                "classification_hints": [hint],
            }
            finding_id = finding_ids_by_name.get(finding_name)
            if finding_id is not None:
                classification_default_payload["finding_id"] = finding_id
            if classification_id is not None:
                classification_default_payload["classification_id"] = classification_id
            requirement_defaults[str(req_id)] = [classification_default_payload]

            if ok:
                continue

            finding_observations = list(observations_by_finding.get(finding_name, []))
            classification_action: dict[str, Any] = {
                "type": "add_classification" if finding_observations else "add_finding",
                "finding_name": finding_name,
                "classification_name": classification_name,
                "classification_names": [classification_name],
                "classification_hints": [hint],
                "classification_ids": (
                    [classification_id] if classification_id is not None else []
                ),
                "note": (
                    str((evaluation_result.get("issues") or [{}])[0].get("code"))
                    if evaluation_result.get("issues")
                    else "classification_validator_unsatisfied"
                ),
            }
            if finding_id is not None:
                classification_action["finding_id"] = finding_id
            suggested_actions[str(req_id)] = [classification_action]

        for validator_name in exam_validator_names:
            req_lookup_name = f"{template_name}:examination_validator:{validator_name}"
            req_id = requirement_id_by_lookup_name[req_lookup_name]
            current_set_requirements.append(
                {"id": req_id, "name": f"examination_validator:{validator_name}"}
            )

            evaluation_result = evaluate_exam_validator_by_name(set_id, validator_name)
            ok = bool(evaluation_result.get("ok"))
            current_set_statuses.append(ok)
            requirement_status[str(req_id)] = bool(ok)
            if ok:
                continue
            suggested_actions[str(req_id)] = [
                {
                    "type": "review_validator_chain",
                    "validator": validator_name,
                    "note": (
                        str((evaluation_result.get("issues") or [{}])[0].get("code"))
                        if evaluation_result.get("issues")
                        else "examination_validator_unsatisfied"
                    ),
                }
            ]

        for validator_name in intervention_validator_names:
            evaluation_result = evaluate_intervention_validator_by_name(validator_name)
            req_lookup_name = f"{template_name}:intervention_validator:{validator_name}"
            req_id = requirement_id_by_lookup_name[req_lookup_name]
            current_set_requirements.append(
                {"id": req_id, "name": f"intervention_validator:{validator_name}"}
            )
            ok = bool(evaluation_result.get("ok"))
            current_set_statuses.append(ok)
            requirement_status[str(req_id)] = ok
            if ok:
                continue
            suggested_actions[str(req_id)] = [
                {
                    "type": "add_intervention",
                    "finding_name": evaluation_result.get("finding"),
                    "intervention_name": evaluation_result.get("intervention"),
                    "intervention_hints": [evaluation_result.get("hint")],
                    "note": (
                        str((evaluation_result.get("issues") or [{}])[0].get("code"))
                        if evaluation_result.get("issues")
                        else "intervention_validator_unsatisfied"
                    ),
                }
            ]

        for validator_name in unit_validator_names:
            evaluation_result = evaluate_unit_validator_by_name(validator_name)
            req_lookup_name = f"{template_name}:unit_validator:{validator_name}"
            req_id = requirement_id_by_lookup_name[req_lookup_name]
            current_set_requirements.append(
                {"id": req_id, "name": f"unit_validator:{validator_name}"}
            )
            ok = bool(evaluation_result.get("ok"))
            current_set_statuses.append(ok)
            requirement_status[str(req_id)] = ok
            if ok:
                continue
            suggested_actions[str(req_id)] = [
                {
                    "type": "add_unit",
                    "finding_name": evaluation_result.get("finding"),
                    "classification_name": evaluation_result.get("classification"),
                    "unit_name": evaluation_result.get("unit"),
                    "unit_hints": [evaluation_result.get("hint")],
                    "note": (
                        str((evaluation_result.get("issues") or [{}])[0].get("code"))
                        if evaluation_result.get("issues")
                        else "unit_validator_unsatisfied"
                    ),
                }
            ]

        requirements_by_set[str(set_id)] = current_set_requirements
        requirement_set_status[str(set_id)] = (
            all(current_set_statuses) if current_set_statuses else True
        )

    candidate_set_ids = sorted(set_ids_to_templates.keys())
    candidate_confidence = 0.8 if candidate_set_ids else 0.0
    if requested_known_ids:
        candidate_confidence = 1.0

    return {
        "requirements_by_set": requirements_by_set,
        "requirement_status": requirement_status,
        "requirement_set_status": requirement_set_status,
        "requirement_defaults": requirement_defaults,
        "classification_choices": classification_choices,
        "suggested_actions": suggested_actions,
        "candidate_requirement_set_ids": candidate_set_ids,
        "candidate_requirement_set_confidence": candidate_confidence,
        "selected_requirement_set_ids": requested_set_ids,
        "history_context": {},
        "advisory_only": True,
    }


def try_build_dtypes_requirement_guidance(
    *,
    pe: PatientExaminationLike,
    selected_requirement_set_ids: list[int] | None = None,
    user_tags: list[str] | None = None,
    use_history_priors: bool = True,
) -> dict[str, Any] | None:
    """
    Build requirement guidance from dtypes report-template validators.

    Returns None when dtypes data is unavailable or no template matches the
    current examination so callers can safely fallback to legacy DB evaluation.
    Raises DtypesKnowledgeBaseResolutionError when the patient examination is
    pinned to a knowledge-base version that is not provisioned locally.
    """
    del user_tags, use_history_priors

    module_name, version = _resolve_requested_kb_identity(pe)
    try:
        kb = _load_dtypes_kb(module_name, version=version)
    except KnowledgeBaseVersionNotFoundError as exc:
        raise DtypesKnowledgeBaseResolutionError(
            "dtypes knowledge-base version is not provisioned locally for "
            f"module '{module_name}' and version '{version}'."
        ) from exc
    except KnowledgeBaseRegistryError as exc:
        raise DtypesKnowledgeBaseResolutionError(
            f"dtypes knowledge-base registry is invalid: {exc}"
        ) from exc
    if kb is None:
        return None

    try:
        return _build_dtypes_requirement_guidance(
            pe=pe,
            kb=kb,
            selected_requirement_set_ids=selected_requirement_set_ids,
        )
    except DtypesKnowledgeBaseResolutionError:
        raise
    except DtypesRequirementEvaluationError as exc:
        logger.warning("dtypes requirement evaluation failed safely: %s", exc)
        return None
    except Exception as exc:
        logger.exception("dtypes requirement evaluation crashed: %s", exc)
        return None


def try_build_dtypes_lookup_updates(
    *,
    pe: PatientExaminationLike,
    selected_requirement_set_ids: list[int],
) -> dict[str, Any] | None:
    """
    Build recompute updates from dtypes report-template validators.

    The payload intentionally mirrors LookupDerivedUpdates keys only.
    """
    guidance = try_build_dtypes_requirement_guidance(
        pe=pe,
        selected_requirement_set_ids=selected_requirement_set_ids,
        use_history_priors=False,
    )
    if guidance is None:
        return None
    return {
        "requirements_by_set": guidance.get("requirements_by_set", {}),
        "requirement_status": guidance.get("requirement_status", {}),
        "requirement_set_status": guidance.get("requirement_set_status", {}),
        "requirement_defaults": guidance.get("requirement_defaults", {}),
        "classification_choices": guidance.get("classification_choices", {}),
        "suggested_actions": guidance.get("suggested_actions", {}),
        "candidate_requirement_set_ids": guidance.get(
            "candidate_requirement_set_ids", []
        ),
        "candidate_requirement_set_confidence": guidance.get(
            "candidate_requirement_set_confidence", 0.0
        ),
    }


__all__ = [
    "LOOKUP_REQUIREMENT_SOURCE_LEGACY_DB",
    "LOOKUP_REQUIREMENT_SOURCE_DTYPES",
    "LOOKUP_REQUIREMENT_SOURCE_HYBRID_COMPARE",
    "LOOKUP_REQUIREMENT_SOURCE_VALUES",
    "DEFAULT_LOOKUP_REQUIREMENT_SOURCE",
    "DEFAULT_LOOKUP_DTYPES_MODULE",
    "DEFAULT_LOOKUP_DTYPES_MODULE_VERSION",
    "DEFAULT_LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED",
    "DtypesKnowledgeBaseResolutionError",
    "get_lookup_requirement_source",
    "get_lookup_dtypes_module_name",
    "get_lookup_dtypes_module_version",
    "get_lookup_requirement_legacy_fallback_enabled",
    "try_build_dtypes_requirement_guidance",
    "try_build_dtypes_lookup_updates",
]
