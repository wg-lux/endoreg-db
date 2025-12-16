"""
Django management command to create ModelMeta from Hugging Face model.
"""

from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from huggingface_hub import hf_hub_download

from endoreg_db.models import AiModel, LabelSet, ModelMeta


class Command(BaseCommand):
    help = "Create ModelMeta by downloading model from Hugging Face"

    def add_arguments(self, parser):
        parser.add_argument(
            "--model_id",
            type=str,
            default="wg-lux/colo_segmentation_RegNetX800MF_base",
            help="Hugging Face model ID",
        )
        parser.add_argument(
            "--model_name",
            type=str,
            default="image_multilabel_classification_colonoscopy_default",
            help="Name for the AI model",
        )
        parser.add_argument(
            "--labelset_name",
            type=str,
            default="multilabel_classification_colonoscopy_default",
            help="Name of the labelset",
        )
        parser.add_argument(
            "--meta_version",
            type=str,
            default="1",
            help="Version for the model meta",
        )
        parser.add_argument(
            "--labelset_version",
            type=int,
            default=None,
            help="LabelSet version; if omitted, the latest by name is used",
        )

    def handle(self, *args, **options):
        model_id = options["model_id"]
        model_name = options["model_name"]
        labelset_name = options["labelset_name"]
        version = options["meta_version"]
        labelset_version = options.get("labelset_version")

        self.stdout.write(f"Downloading model {model_id} from Hugging Face...")

        try:
            # Download the model weights
            weights_path = hf_hub_download(
                repo_id=model_id,
                filename="colo_segmentation_RegNetX800MF_base.safetensors",
                local_dir="/tmp",
            )
            self.stdout.write(f"Downloaded weights to: {weights_path}")

            # Get or create AI model
            ai_model, created = AiModel.objects.get_or_create(
                name=model_name, defaults={"description": f"Model from {model_id}"}
            )
            if created:
                self.stdout.write(f"Created AI model: {ai_model.name}")

            # Get labelset (optionally by version); fail with non-zero exit
            labelset_qs = LabelSet.objects.filter(name=labelset_name)
            if labelset_version is not None:
                labelset_qs = labelset_qs.filter(version=labelset_version)
            labelset = labelset_qs.order_by("-version").first()
            if labelset is None:
                raise CommandError(
                    f"LabelSet '{labelset_name}'"
                    + (f" v{labelset_version}" if labelset_version is not None else "")
                    + " not found"
                )

            # Create ModelMeta
            model_meta, created = ModelMeta.objects.get_or_create(
                name=model_name,
                model=ai_model,
                version=version,
                defaults={
                    "labelset": labelset,
                    "activation": "sigmoid",
                    "mean": "0.45211223,0.27139644,0.19264949",
                    "std": "0.31418097,0.21088019,0.16059452",
                    "size_x": 716,
                    "size_y": 716,
                    "axes": "2,0,1",
                    "batchsize": 16,
                    "num_workers": 0,
                    "description": f"Downloaded from {model_id}",
                },
            )

            # Save the weights file to the model
            with open(weights_path, "rb") as f:
                model_meta.weights.save(
                    f"{model_name}_v{version}.safetensors", ContentFile(f.read())
                )

            # Set as active meta
            ai_model.active_meta = model_meta
            ai_model.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully {'created' if created else 'updated'} ModelMeta: {model_meta}"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error creating ModelMeta: {e}"))
            import traceback

            traceback.print_exc()
            raise CommandError("ModelMeta creation failed") from e
