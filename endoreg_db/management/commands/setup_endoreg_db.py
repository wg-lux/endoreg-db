"""
Django management command to perform complete setup for EndoReg DB when used as an embedded app.
This command ensures all necessary data and configurations are initialized.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser
from django.db.models.fields.files import FieldFile
from lx_dtypes.models.contracts.management_command import (
    SetupEndoregDbCommandOptionsPayload,
)
from lx_dtypes.models.contracts.setup_config import (
    SetupConfigAutoGenerationDefaultsPayload,
)

from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    ensure_directory,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from endoreg_db.models.administration.ai.ai_model import AiModel
    from endoreg_db.models.label.label_set import LabelSet


class _SetupModelMeta(Protocol):
    name: str
    version: str
    weights: FieldFile

    def save(self, *, update_fields: list[str]) -> None: ...


@dataclass(frozen=True)
class _MetadataRepairContext:
    defaults: SetupConfigAutoGenerationDefaultsPayload
    primary_labelset_name: str


class Command(BaseCommand):
    help = """
    Complete setup for EndoReg DB when used as an embedded app.
    This command performs all necessary initialization steps:
    1. Loads base database data
    2. Sets up caching (if using db cache)
    3. Loads default models from setup configuration file (setup_config.yaml)
    4. Loads models according to fallback chain (Local Files -> HuggingFace -> graceful failure)
    5. Initializes model metadata
    """

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--skip-ai-setup",
            action="store_true",
            help="Skip AI model setup (for cases where AI features are not needed)",
        )
        parser.add_argument(
            "--force-recreate",
            action="store_true",
            help="Force recreation of AI model metadata even if it exists",
        )
        parser.add_argument(
            "--yaml-only",
            action="store_true",
            help="Only use YAML-defined models, don't auto-generate missing metadata",
        )

    def handle(self, *args: object, **options: object) -> None:
        options_payload = SetupEndoregDbCommandOptionsPayload.model_validate(options)
        self._run_setup_workflow(options_payload)

    def _run_setup_workflow(
        self,
        options: SetupEndoregDbCommandOptionsPayload,
    ) -> None:
        self.stdout.write(
            self.style.SUCCESS("🚀 Starting EndoReg DB embedded app setup...")
        )

        if options.yaml_only:
            self.stdout.write(
                self.style.WARNING(
                    "📋 YAML-only mode: Will not auto-generate missing metadata"
                )
            )

        self.stdout.write("\n📊 Step 1: Loading base database data...")
        if not self._call_management_command(
            "load_base_db_data",
            success_message="✅ Base database data loaded successfully",
            error_prefix="❌ Failed to load base data",
        ):
            return

        if not self._setup_cache():
            return

        if options.skip_ai_setup:
            self.stdout.write(self.style.WARNING("\n⚠️  Skipping AI setup as requested"))
        elif not self._setup_ai(
            force_recreate=options.force_recreate,
            yaml_only=options.yaml_only,
        ):
            return

        if not self._run_verification():
            return
        self._write_completion()

    def _call_management_command(
        self,
        command_name: str,
        *,
        success_message: str,
        error_prefix: str,
    ) -> bool:
        try:
            call_command(command_name)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"{error_prefix}: {exc}"))
            return False
        self.stdout.write(self.style.SUCCESS(success_message))
        return True

    def _setup_cache(self) -> bool:
        self.stdout.write("\n💾 Step 2: Setting up caching...")
        from django.conf import settings

        cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")
        if "db" not in cache_backend and "database" not in cache_backend:
            self.stdout.write("Using in-memory caching - skipping cache table creation")
            return True
        self.stdout.write("Using database caching - creating cache table...")
        return self._call_management_command(
            "createcachetable",
            success_message="✅ Cache table created successfully",
            error_prefix="❌ Failed to create cache table",
        )

    def _setup_ai(self, *, force_recreate: bool, yaml_only: bool) -> bool:
        self.stdout.write("\n🤖 Step 3: Loading AI model data...")
        if not self._call_management_command(
            "load_ai_model_data",
            success_message="✅ AI model data loaded successfully",
            error_prefix="❌ Failed to load AI model data",
        ):
            return False

        self.stdout.write("\n🏷️  Step 4: Loading AI model label data...")
        if not self._call_management_command(
            "load_ai_model_label_data",
            success_message="✅ AI model label data loaded successfully",
            error_prefix="❌ Failed to load AI model label data",
        ):
            return False

        if not self._setup_primary_model_metadata(force_recreate=force_recreate):
            return False
        return self._run_metadata_validation(yaml_only=yaml_only)

    def _setup_primary_model_metadata(self, *, force_recreate: bool) -> bool:
        self.stdout.write("\n📋 Step 5: Creating AI model metadata...")
        try:
            return self._create_primary_model_metadata(force_recreate=force_recreate)
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"❌ Failed to create AI model metadata: {exc}")
            )
            return False

    def _create_primary_model_metadata(self, *, force_recreate: bool) -> bool:
        from endoreg_db.models.administration.ai.ai_model import AiModel
        from endoreg_db.utils.setup_config import setup_config

        default_model_name = setup_config.get_primary_model_name()
        primary_labelset = setup_config.get_primary_labelset_name()
        ai_model = AiModel.objects.filter(name=default_model_name).first()
        if not ai_model:
            self.stdout.write(
                self.style.ERROR(f"❌ AI model '{default_model_name}' not found")
            )
            return False

        existing_meta = ai_model.metadata_versions.first()
        if existing_meta and not force_recreate:
            self.stdout.write(
                self.style.SUCCESS(
                    "✅ Model metadata already exists (use --force-recreate to recreate)"
                )
            )
            return True

        model_path = self._find_model_weights_file()
        if not model_path:
            self.stdout.write(
                self.style.WARNING(
                    "⚠️  Model weights file not found. AI features may not work properly."
                )
            )
            return True

        call_command_kwargs: dict[str, object] = {
            "model_name": default_model_name,
            "model_meta_version": 1,
            "image_classification_labelset_name": primary_labelset,
            "model_path": str(model_path),
        }
        if force_recreate:
            call_command_kwargs["bump_version"] = True
        call_command("create_multilabel_model_meta", **call_command_kwargs)
        self.stdout.write(
            self.style.SUCCESS("✅ AI model metadata created successfully")
        )
        return True

    def _run_metadata_validation(self, *, yaml_only: bool) -> bool:
        self.stdout.write("\n🔧 Step 5.5: Validating AI model active metadata...")
        try:
            self._validate_and_fix_ai_model_metadata(yaml_only)
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"❌ Failed to validate AI model metadata: {exc}")
            )
            return False
        self.stdout.write(
            self.style.SUCCESS("✅ AI model metadata validation completed")
        )
        return True

    def _run_verification(self) -> bool:
        self.stdout.write("\n🔍 Step 6: Verifying setup...")
        try:
            self._verify_setup()
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"❌ Setup verification failed: {exc}"))
            return False
        self.stdout.write(
            self.style.SUCCESS("✅ Setup verification completed successfully")
        )
        return True

    def _write_completion(self) -> None:
        self.stdout.write(
            self.style.SUCCESS(
                "\n🎉 EndoReg DB embedded app setup completed successfully!"
            )
        )
        self.stdout.write("\nNext steps:")
        # self.stdout.write("1. Run migrations: python manage.py migrate")
        self.stdout.write("1. Create superuser: python manage.py createsuperuser")
        self.stdout.write("2. Start development server: python manage.py runserver")

    def _find_model_weights_file(self):
        """Find the model weights file using configurable search patterns and directories."""
        # Load setup configuration
        from endoreg_db.utils.setup_config import setup_config

        # First try to find weights using configured patterns
        found_files = setup_config.find_model_weights_files()
        if found_files:
            self.stdout.write(f"Found model weights at: {found_files[0]}")
            return found_files[0]

        # If no local weights found and HuggingFace fallback is enabled
        hf_config = setup_config.get_huggingface_config()
        if hf_config.enabled:
            self.stdout.write(
                "📦 No local model weights found — attempting HuggingFace download..."
            )
            try:
                if not ModelMeta.objects.exists():
                    ModelMeta.setup_default_from_huggingface(
                        hf_config.repo_id,
                        labelset_name=hf_config.labelset_name,
                    )
                    self.stdout.write("✅ Default ModelMeta created from HuggingFace.")

                    # Try to find the downloaded weights
                    found_files = setup_config.find_model_weights_files()
                    if found_files:
                        return found_files[0]

            except Exception as e:
                self.stdout.write(f"⚠️  HuggingFace download failed: {e}")

        self.stdout.write("Model weights file not found in configured locations")
        return None

    def _verify_setup(self):
        """Verify that the setup was successful."""
        from django.conf import settings
        from django.db import connection

        # Check that required tables exist
        required_tables = [
            "endoreg_db_aimodel",
            "endoreg_db_modelmeta",
        ]

        # Only check for cache table if using database caching
        cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")
        if "db" in cache_backend or "database" in cache_backend:
            required_tables.append("django_cache_table")

        cursor = connection.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        existing_tables = [row[0] for row in cursor.fetchall()]

        missing_tables = [
            table for table in required_tables if table not in existing_tables
        ]
        if missing_tables:
            raise Exception(f"Missing required tables: {missing_tables}")

        # Check that AI models exist (if AI setup was performed)
        from endoreg_db.models.administration.ai.ai_model import AiModel

        if AiModel.objects.exists():
            ai_model_count = AiModel.objects.count()
            self.stdout.write(f"Found {ai_model_count} AI model(s)")

            # Check for model metadata
            from endoreg_db.models.metadata.model_meta import ModelMeta

            meta_count = ModelMeta.objects.count()
            self.stdout.write(f"Found {meta_count} model metadata record(s)")

        self.stdout.write("Setup verification passed")

    def _validate_and_fix_ai_model_metadata(self, yaml_only: bool = False) -> None:
        """
        Validate that all AI models have proper active metadata and fix if necessary.
        This addresses the "No model metadata found for this model" error.

        Args:
            yaml_only (bool): If True, only set active metadata but don't create new metadata
        """
        from endoreg_db.models.administration.ai.ai_model import AiModel
        from endoreg_db.utils.setup_config import setup_config

        all_models = AiModel.objects.all()
        context = _MetadataRepairContext(
            defaults=setup_config.get_auto_generation_defaults(),
            primary_labelset_name=setup_config.get_primary_labelset_name(),
        )
        fixed_count = sum(
            self._repair_model_metadata(
                model,
                yaml_only=yaml_only,
                context=context,
            )
            for model in all_models
        )
        self.stdout.write("\nTesting model metadata access...")
        self._verify_latest_metadata(all_models)
        if fixed_count > 0:
            self.stdout.write(f"Fixed metadata for {fixed_count} model(s)")
        else:
            self.stdout.write("All models already had proper metadata")

    def _repair_model_metadata(
        self,
        model: AiModel,
        *,
        yaml_only: bool,
        context: _MetadataRepairContext,
    ) -> int:
        self.stdout.write(f"Checking model: {model.name}")
        metadata_count = model.metadata_versions.count()
        self.stdout.write(f"  Metadata versions: {metadata_count}")

        if metadata_count == 0:
            if yaml_only:
                self.stdout.write(
                    f"  ⚠️  YAML-only mode: Skipping auto-generation for {model.name}"
                )
                return 0
            self._create_missing_model_metadata(model, context=context)
            return 1

        if model.active_meta:
            self.stdout.write(
                f"  ✅ Model {model.name} has active metadata: {model.active_meta}"
            )
            return 0

        first_meta = model.metadata_versions.first()
        if not first_meta:
            self.stdout.write(f"  ⚠️  No metadata versions available for {model.name}")
            return 0
        self._activate_existing_metadata(
            model,
            first_meta=cast(_SetupModelMeta, first_meta),
        )
        return 1

    def _create_missing_model_metadata(
        self,
        model: AiModel,
        *,
        context: _MetadataRepairContext,
    ) -> None:
        from endoreg_db.models.metadata.model_meta import ModelMeta

        self.stdout.write(f"  Creating metadata for {model.name}...")
        labelset = self._resolve_generation_labelset(context.primary_labelset_name)
        weights_path = self._optional_weights_path(copied_indent="    ")
        defaults = context.defaults
        meta = ModelMeta.objects.create(
            name=model.name,
            version="1.0",
            model=model,
            labelset=labelset,
            weights=weights_path,
            activation=defaults.activation,
            mean=defaults.mean,
            std=defaults.std,
            size_x=defaults.size_x,
            size_y=defaults.size_y,
            axes=defaults.axes,
            batchsize=defaults.batchsize,
            num_workers=defaults.num_workers,
            description=f"Auto-generated metadata for {model.name}",
        )
        model.active_meta = meta
        model.save()
        self.stdout.write(f"  ✅ Created and set metadata for {model.name}")

    @staticmethod
    def _resolve_generation_labelset(primary_labelset_name: str) -> LabelSet:
        from endoreg_db.models.label.label_set import LabelSet

        try:
            return LabelSet.objects.get(name=primary_labelset_name)
        except LabelSet.DoesNotExist:
            labelset = LabelSet.objects.first()
            if labelset:
                return labelset
            return LabelSet.objects.create(
                name="default_colonoscopy_labels",
                description="Default colonoscopy classification labels",
            )

    def _optional_weights_path(self, *, copied_indent: str) -> str:
        weights_file = self._find_model_weights_file()
        if not weights_file:
            return ""
        return self._storage_relative_weights_path(
            Path(weights_file),
            copied_indent=copied_indent,
        )

    def _storage_relative_weights_path(
        self,
        weights_file: Path,
        *,
        copied_indent: str,
    ) -> str:
        from endoreg_db.utils.paths import STORAGE_DIR

        try:
            return str(weights_file.relative_to(STORAGE_DIR))
        except ValueError:
            weights_dir = STORAGE_DIR / "model_weights"
            ensure_directory(weights_dir)
            dest_path = weights_dir / weights_file.name
            atomic_copy_file(
                source=weights_file,
                destination=dest_path,
            )
            self.stdout.write(f"{copied_indent}Copied weights to: {dest_path}")
            return str(dest_path.relative_to(STORAGE_DIR))

    def _activate_existing_metadata(
        self,
        model: AiModel,
        *,
        first_meta: _SetupModelMeta,
    ) -> None:
        self.stdout.write(f"  Setting active metadata for {model.name}...")
        if not first_meta.weights:
            self.stdout.write(
                "    Metadata exists but no weights assigned, attempting to add weights..."
            )
            self._add_weights_to_metadata(first_meta)

        model.active_meta = first_meta
        model.save()
        self.stdout.write(
            f"  ✅ Set active metadata: {first_meta.name} v{first_meta.version}"
        )

    def _add_weights_to_metadata(self, model_meta: _SetupModelMeta) -> None:
        weights_path = self._optional_weights_path(copied_indent="      ")
        if not weights_path:
            return
        model_meta.weights.name = weights_path
        model_meta.save(update_fields=["weights"])
        self.stdout.write(f"      Added weights to existing metadata: {weights_path}")

    def _verify_latest_metadata(self, models: QuerySet[AiModel]) -> None:
        for model in models:
            try:
                latest = model.get_latest_version()
                self.stdout.write(f"  ✅ {model.name}: {latest}")
            except Exception as exc:
                self.stdout.write(f"  ❌ {model.name}: {exc}")
                raise Exception(f"Model {model.name} still has metadata issues: {exc}")
