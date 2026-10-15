"""YAML-configured finding mentions; no automatic clinical confirmation."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from django.db import transaction
from lx_dtypes.models.contracts.finding_extraction import (
    FindingExtractionEvidence,
    FindingMention,
    FindingTextRule,
)
from lx_dtypes.terminology.terminology_loader import get_terminology_service

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

if TYPE_CHECKING:
    from spacy.language import Language


def extract_finding_mentions(
    text: str,
    findings: Mapping[str, Sequence[FindingTextRule]],
    *,
    nlp: "Language | None" = None,
) -> list[FindingMention]:
    """Match explicit token rules; longer assertions suppress contained matches.

    For example, a configured ``kein Polyp`` absent match suppresses a contained
    ``Polyp`` present match. Equal spans with differing assertions remain separate
    evidence, never silently resolved to a positive finding.
    """
    if len(text) > 1_000_000:
        raise ValueError("Report text exceeds the finding-extraction limit")
    if not any(findings.values()):
        return []
    from spacy.matcher import Matcher

    if nlp is None:
        from lx_anonymizer.ner.spacy_extractor import SpacyModelManager

        nlp = SpacyModelManager.get_model()
    matcher = Matcher(nlp.vocab, validate=True)
    rules: dict[int, tuple[str, FindingTextRule]] = {}
    for finding, definitions in findings.items():
        for definition in definitions:
            rule = FindingTextRule.model_validate(definition.model_dump())
            key = f"finding_rule_{len(rules)}"
            matcher.add(key, [rule.pattern])
            rules[nlp.vocab.strings[key]] = (finding, rule)
    # Invocation-owned document and matcher prevent state leaking across imports.
    document = nlp(text)
    mentions: list[FindingMention] = []
    for match_id, start, end in matcher(document):
        finding, rule = rules[match_id]
        span = document[start:end]
        mention = FindingMention(
            finding=finding,
            presence=rule.presence,
            start=span.start_char,
            end=span.end_char,
        )
        if mention not in mentions:
            mentions.append(mention)
    return sorted(
        (
            mention
            for mention in mentions
            if not any(
                other.finding == mention.finding
                and other.start <= mention.start
                and other.end >= mention.end
                and (other.start < mention.start or other.end > mention.end)
                for other in mentions
            )
        ),
        key=lambda mention: (
            mention.start,
            mention.end,
            mention.finding,
            mention.presence,
        ),
    )


def record_report_finding_presence(report: RawPdfFile) -> None:
    """Persist source-bound evidence within the caller's publication transaction.

    An unselected registry disables this optional enrichment. Once a package is
    selected, invalid configuration fails the import instead of losing evidence.
    """
    service = get_terminology_service()
    examination = report.examination
    if examination is None and report.sensitive_meta is not None:
        examination = report.sensitive_meta.pseudo_examination
    module = examination.knowledge_base_module if examination is not None else ""
    version = examination.knowledge_base_version if examination is not None else ""
    if bool(module) != bool(version):
        raise ValueError("Finding extraction requires a complete terminology identity")
    # Absence of a registry is an explicitly unconfigured optional feature.
    # A pinned examination must still resolve, even when its registry is missing.
    if not module and not service.registry_path.exists():
        return
    identity = (module, version) if module else service.active_identity()
    if identity is None:
        return
    knowledge_base = service.load(*identity)
    rules = {
        name: finding.text_extraction
        for name, finding in knowledge_base.finding.items()
        if finding.text_extraction
    }
    if not rules:
        return
    text = report.anonymized_text
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "Configured finding extraction requires anonymized report text"
        )
    evidence = FindingExtractionEvidence(
        knowledge_base_module=identity[0],
        knowledge_base_version=identity[1],
        mentions=extract_finding_mentions(text, rules),
    )
    with transaction.atomic():
        locked = RawPdfFile.objects.select_for_update().get(pk=report.pk)
        if locked.anonymized_text != text:
            raise ValueError("Report text changed during finding extraction")
        metadata = dict(locked.raw_meta or {})
        metadata["finding_extraction"] = evidence.model_dump(mode="json")
        locked.raw_meta = metadata
        locked.save(update_fields=["raw_meta"])
        report.raw_meta = locked.raw_meta
