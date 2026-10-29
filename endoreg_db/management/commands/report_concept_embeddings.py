"""Capture explicit anonymized report span annotations or export similarity CSV."""

import csv
import json
import sys
from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from lx_dtypes.models.contracts.text_embedding import ReportConceptSpan

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.report.concept_embedding import ReportConceptEmbedding
from endoreg_db.services.reports.concept_embeddings import (
    SIMILARITY_COLUMNS,
    SpacyReportTextEmbedder,
    anonymized_report_hash,
    capture_report_concept_embedding,
    default_report_text_embedder,
    similarity_rows,
)


class Command(BaseCommand):
    help = "Persist vectors only from validated anonymized report spans; export annotated-span similarities."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("operation", choices=["source", "capture", "export"])
        parser.add_argument("--center-id", required=True, type=int)
        parser.add_argument("--report-id", type=int)
        parser.add_argument("--database", default="default")
        parser.add_argument(
            "--spacy-model",
            help=(
                "Override: installed local spaCy package or model directory with "
                "static word vectors; no downloads. Default: de_core_news_lg 3.8.0 at "
                "ENDOREG_REPORT_EMBEDDING_MODEL_PATH (provisioned by deployment)"
            ),
        )
        parser.add_argument("--top-k", type=int, default=20)

    def handle(self, *args: object, **options: object) -> None:
        database = str(options["database"])
        reports = RawPdfFile.objects.using(database).filter(
            center_id=options["center_id"]
        )
        if options["report_id"] is not None:
            reports = reports.filter(pk=options["report_id"])
        try:
            if options["operation"] == "source":
                if options["report_id"] is None:
                    raise CommandError("source requires --report-id")
                report = reports.get()
                self.stdout.write(
                    json.dumps(
                        {
                            "report_id": report.pk,
                            "source_hash": anonymized_report_hash(report),
                        }
                    )
                )
            elif options["operation"] == "capture":
                payload = sys.stdin.read(16385)
                if len(payload) > 16384:
                    raise CommandError("Annotation request exceeds 16384 characters")
                request = ReportConceptSpan.model_validate_json(payload)
                # Check source scope before loading a potentially large local model.
                reports.get(pk=request.report_id)
                if options["spacy_model"]:
                    import spacy

                    nlp = spacy.load(str(options["spacy_model"]))
                    provider = SpacyReportTextEmbedder(
                        nlp,
                        model=str(options["spacy_model"]),
                        revision=str(nlp.meta["version"]),
                    )
                else:
                    provider = default_report_text_embedder()
                record = capture_report_concept_embedding(
                    request, report_scope=reports, embedder=provider
                )
                self.stdout.write(
                    json.dumps(
                        {"embedding_id": record.pk, "space_key": record.space_key}
                    )
                )
            else:
                records = ReportConceptEmbedding.objects.using(database).filter(
                    report__in=reports
                )
                top_k = options["top_k"]
                if not isinstance(top_k, int):
                    raise CommandError("top_k must be an integer")
                rows = list(similarity_rows(records, top_k=top_k))
                writer = csv.DictWriter(self.stdout, fieldnames=SIMILARITY_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
        except (ValueError, RuntimeError, ObjectDoesNotExist, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
