"""Resolve reviewable evidence without creating clinical findings."""

from uuid import NAMESPACE_URL, uuid5
from typing import cast

from lx_dtypes.models.contracts.finding_extraction import FindingExtractionEvidence
from lx_dtypes.models.contracts.finding_review import (
    AnnotationFindingMappings,
    FindingSuggestion,
    FindingSuggestions,
)
from lx_dtypes.terminology.terminology_loader import (
    get_terminology_service,
    resolve_module_path,
)
from lx_dtypes.utils.study_setup_yaml import parse_setup_yaml
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_finding import PatientFinding


def finding_suggestions(media: RawPdfFile | VideoFile) -> FindingSuggestions:
    examination = media.examination
    if examination is None and media.sensitive_meta is not None:
        examination = media.sensitive_meta.pseudo_examination
    examination_id = cast(int, examination.pk) if examination is not None else None
    result = FindingSuggestions(examination=examination_id)
    evidence: dict[str, list[str]] = {}
    assertions: dict[str, set[str]] = {}
    if isinstance(media, RawPdfFile):
        raw = (media.raw_meta or {}).get("finding_extraction")
        if raw is None:
            result.reason = "Keine erkannten Befunde aus dem Import vorhanden."
            return result
        extracted = FindingExtractionEvidence.model_validate(raw)
        module, version = (
            extracted.knowledge_base_module,
            extracted.knowledge_base_version,
        )
        if (
            examination is not None
            and examination.knowledge_base_module
            and (examination.knowledge_base_module, examination.knowledge_base_version)
            != (module, version)
        ):
            raise ValueError(
                "Report and examination use different terminology versions"
            )
        for mention in extracted.mentions:
            text = media.anonymized_text or ""
            if mention.end > len(text):
                raise ValueError("Finding evidence no longer matches report text")
            evidence.setdefault(mention.finding, []).append(
                text[mention.start : mention.end]
            )
            assertions.setdefault(mention.finding, set()).add(mention.presence)
        source = "report"
    else:
        # Model-backed segments are proof that inference actually produced labels.
        predictions = LabelVideoSegment.objects.filter(
            video_file=media,
            prediction_meta__isnull=False,
            prediction_meta__model_meta__labelset__isnull=False,
        )
        frames = ImageClassificationAnnotation.objects.filter(
            frame__video=media,
            model_meta__isnull=False,
            value=True,
        )
        if not predictions.exists() and not frames.exists():
            result.reason = "Keine Befundvorschläge aus einer Bild-KI vorhanden."
            return result
        module = examination.knowledge_base_module if examination is not None else ""
        version = examination.knowledge_base_version if examination is not None else ""
        if bool(module) != bool(version):
            raise ValueError("Incomplete examination terminology identity")
        service = get_terminology_service()
        identity = (module, version) if module else service.active_identity()
        if identity is None:
            result.reason = "Keine Terminologie für die Label-Zuordnung ausgewählt."
            return result
        module, version = identity
        path = resolve_module_path(module, version=version) / "annotation_mappings.yml"
        if not path.exists():
            result.reason = (
                "Keine Label-zu-Befund-Zuordnung für diese Terminologie vorhanden."
            )
            return result
        mapping = AnnotationFindingMappings.model_validate(
            parse_setup_yaml(path.read_text())
        )
        if (mapping.knowledge_base_module, mapping.knowledge_base_version) != (
            module,
            version,
        ):
            raise ValueError(
                "Label mapping does not match the selected terminology version"
            )
        labels = set(
            predictions.filter(
                prediction_meta__model_meta__labelset__name__in=mapping.labelset_names,
            ).values_list("label__name", flat=True)
        )
        frame_labels = set(
            frames.filter(
                model_meta__labelset__name__in=mapping.labelset_names,
            ).values_list("label__name", flat=True)
        )
        for rule in mapping.mappings:
            if ("segment" in rule.modalities and rule.label in labels) or (
                "frame" in rule.modalities and rule.label in frame_labels
            ):
                evidence.setdefault(rule.finding_name, []).append(rule.label)
                assertions.setdefault(rule.finding_name, set()).add("present")
        source = "prediction"
    kb = get_terminology_service().load(module, version)
    for name, snippets in evidence.items():
        if name not in kb.finding:
            raise ValueError("Finding evidence refers to unknown terminology")
        finding = Finding.objects.get(name=name)
        instance = uuid5(
            NAMESPACE_URL,
            f"media-finding:{source}:{media.pk}:{examination_id}:{module}:{version}:{name}",
        )
        statuses = assertions[name]
        presence = (
            "present"
            if statuses == {"present"}
            else "absent"
            if statuses == {"absent"}
            else "uncertain"
        )
        result.candidates.append(
            FindingSuggestion(
                id=cast(int, finding.pk),
                name=name,
                title=kb.finding[name].name_de or name,
                source=source,
                presence=presence,
                evidence=list(dict.fromkeys(snippets)),
                instance=instance,
                approved=PatientFinding.objects.filter(
                    instance_id=instance, is_active=True
                ).exists(),
            )
        )
    return result
