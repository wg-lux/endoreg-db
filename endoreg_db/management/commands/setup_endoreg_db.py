"""
Django management command to perform complete setup for EndoReg DB when used as an embedded app.
This command ensures all necessary data and configurations are initialized.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from endoreg_db.models import ModelMeta


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

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        skip_ai = options.get("skip_ai_setup", False)
        force_recreate = options.get("force_recreate", False)
        yaml_only = options.get("yaml_only", False)

        self.stdout.write(self.style.SUCCESS("🚀 Starting EndoReg DB embedded app setup..."))

        if yaml_only:
            self.stdout.write(self.style.WARNING("📋 YAML-only mode: Will not auto-generate missing metadata"))

        # Step 1: Load base database data
        self.stdout.write("\n📊 Step 1: Loading base database data...")
        try:
            call_command("load_base_db_data")
            self.stdout.write(self.style.SUCCESS("✅ Base database data loaded successfully"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Failed to load base data: {e}"))
            return

        # Step 2: Create cache table (only if using database caching)
        self.stdout.write("\n💾 Step 2: Setting up caching...")
        from django.conf import settings

        cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")
        if "db" in cache_backend or "database" in cache_backend:
            self.stdout.write("Using database caching - creating cache table...")
            try:
                call_command("createcachetable")
                self.stdout.write(self.style.SUCCESS("✅ Cache table created successfully"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed to create cache table: {e}"))
                return
        else:
            self.stdout.write("Using in-memory caching - skipping cache table creation")

        if skip_ai:
            self.stdout.write(self.style.WARNING("\n⚠️  Skipping AI setup as requested"))
        else:
            # Step 3: Load AI model data
            self.stdout.write("\n🤖 Step 3: Loading AI model data...")
            try:
                call_command("load_ai_model_data")
                self.stdout.write(self.style.SUCCESS("✅ AI model data loaded successfully"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed to load AI model data: {e}"))
                return

            # Step 4: Load AI model label data
            self.stdout.write("\n🏷️  Step 4: Loading AI model label data...")
            try:
                call_command("load_ai_model_label_data")
                self.stdout.write(self.style.SUCCESS("✅ AI model label data loaded successfully"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed to load AI model label data: {e}"))
                return

            # Step 5: Create model metadata
            self.stdout.write("\n📋 Step 5: Creating AI model metadata...")
            try:
                # Load setup configuration
                from endoreg_db.utils.setup_config import setup_config

                # Get primary model from configuration
                default_model_name = setup_config.get_primary_model_name()
                primary_labelset = setup_config.get_primary_labelset_name()

                # Check if model metadata already exists
                from endoreg_db.models import AiModel

                ai_model = AiModel.objects.filter(name=default_model_name).first()

                if not ai_model:
                    self.stdout.write(self.style.ERROR(f"❌ AI model '{default_model_name}' not found"))
                    return

                existing_meta = ai_model.metadata_versions.first()
                if existing_meta and not force_recreate:
                    self.stdout.write(self.style.SUCCESS("✅ Model metadata already exists (use --force-recreate to recreate)"))
                else:
                    # Try to create model metadata using configurable approach
                    model_path = self._find_model_weights_file()
                    if model_path:
                        call_command_kwargs = {
                            "model_name": default_model_name,
                            "model_meta_version": 1,
                            "image_classification_labelset_name": primary_labelset,
                            "model_path": str(model_path),
                        }
                        # Add bump_version flag if force_recreate is enabled
                        if force_recreate:
                            call_command_kwargs["bump_version"] = True

                        call_command("create_multilabel_model_meta", **call_command_kwargs)
                        self.stdout.write(self.style.SUCCESS("✅ AI model metadata created successfully"))
                    else:
                        self.stdout.write(self.style.WARNING("⚠️  Model weights file not found. AI features may not work properly."))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed to create AI model metadata: {e}"))
                return

            # Step 5.5: Validate and fix AI model active metadata
            self.stdout.write("\n🔧 Step 5.5: Validating AI model active metadata...")
            try:
                self._validate_and_fix_ai_model_metadata(yaml_only)
                self.stdout.write(self.style.SUCCESS("✅ AI model metadata validation completed"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed to validate AI model metadata: {e}"))
                return

        # Step 6: Verification
        self.stdout.write("\n🔍 Step 6: Verifying setup...")
        try:
            self._verify_setup()
            self.stdout.write(self.style.SUCCESS("✅ Setup verification completed successfully"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Setup verification failed: {e}"))
            return

        self.stdout.write(self.style.SUCCESS("\n🎉 EndoReg DB embedded app setup completed successfully!"))
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
        if hf_config.get("enabled", True):
            self.stdout.write("📦 No local model weights found — attempting HuggingFace download...")
            try:
                if not ModelMeta.objects.exists():
                    ModelMeta.setup_default_from_huggingface(
                        hf_config.get("repo_id", "wg-lux/colo_segmentation_RegNetX800MF_base"),
                        labelset_name=hf_config.get("labelset_name", "multilabel_classification_colonoscopy_default"),
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

        missing_tables = [table for table in required_tables if table not in existing_tables]
        if missing_tables:
            raise Exception(f"Missing required tables: {missing_tables}")

        # Check that AI models exist (if AI setup was performed)
        from endoreg_db.models import AiModel

        if AiModel.objects.exists():
            ai_model_count = AiModel.objects.count()
            self.stdout.write(f"Found {ai_model_count} AI model(s)")

            # Check for model metadata
            from endoreg_db.models import ModelMeta

            meta_count = ModelMeta.objects.count()
            self.stdout.write(f"Found {meta_count} model metadata record(s)")

        self.stdout.write("Setup verification passed")

    def _validate_and_fix_ai_model_metadata(self, yaml_only=False):
        """
        Validate that all AI models have proper active metadata and fix if necessary.
        This addresses the "No model metadata found for this model" error.

        Args:
            yaml_only (bool): If True, only set active metadata but don't create new metadata
        """
        from endoreg_db.models import AiModel, LabelSet, ModelMeta
        from endoreg_db.utils.setup_config import setup_config

        all_models = AiModel.objects.all()
        fixed_count = 0

        # Get configurable defaults
        defaults = setup_config.get_auto_generation_defaults()
        primary_labelset_name = setup_config.get_primary_labelset_name()

        for model in all_models:
            self.stdout.write(f"Checking model: {model.name}")

            # Check if model has metadata versions
            metadata_count = model.metadata_versions.count()
            self.stdout.write(f"  Metadata versions: {metadata_count}")

            if metadata_count == 0:
                if yaml_only:
                    self.stdout.write(f"  ⚠️  YAML-only mode: Skipping auto-generation for {model.name}")
                    continue

                # Create metadata for models that don't have any
                self.stdout.write(f"  Creating metadata for {model.name}...")

                # Use configured labelset or create default
                labelset = None
                try:
                    labelset = LabelSet.objects.get(name=primary_labelset_name)
                except LabelSet.DoesNotExist:
                    labelset = LabelSet.objects.first()
                    if not labelset:
                        labelset = LabelSet.objects.create(name="default_colonoscopy_labels", description="Default colonoscopy classification labels")

                # Create basic metadata WITH weights if available
                weights_file = self._find_model_weights_file()
                weights_path = ""
                if weights_file:
                    # If we have weights, set up the relative path
                    from pathlib import Path

                    from endoreg_db.utils.paths import STORAGE_DIR

                    try:
                        weights_path = str(Path(weights_file).relative_to(STORAGE_DIR))
                    except ValueError:
                        # If file is not in storage dir, copy it there
                        import shutil

                        weights_dir = STORAGE_DIR / "model_weights"
                        weights_dir.mkdir(parents=True, exist_ok=True)
                        dest_path = weights_dir / Path(weights_file).name
                        shutil.copy2(weights_file, dest_path)
                        weights_path = str(dest_path.relative_to(STORAGE_DIR))
                        self.stdout.write(f"    Copied weights to: {dest_path}")

                # Create basic metadata using configurable defaults
                meta = ModelMeta.objects.create(
                    name=model.name,
                    version="1.0",
                    model=model,
                    labelset=labelset,
                    weights=weights_path,  # Set weights if available
                    activation=defaults.get("activation", "sigmoid"),
                    mean=defaults.get("mean", "0.485,0.456,0.406"),
                    std=defaults.get("std", "0.229,0.224,0.225"),
                    size_x=defaults.get("size_x", 224),
                    size_y=defaults.get("size_y", 224),
                    axes=defaults.get("axes", "CHW"),
                    batchsize=defaults.get("batchsize", 32),
                    num_workers=defaults.get("num_workers", 4),
                    description=f"Auto-generated metadata for {model.name}",
                )

                model.active_meta = meta
                model.save()
                fixed_count += 1
                self.stdout.write(f"  ✅ Created and set metadata for {model.name}")

            elif not model.active_meta:
                # Model has metadata but no active meta set
                first_meta = model.metadata_versions.first()
                if first_meta:
                    self.stdout.write(f"  Setting active metadata for {model.name}...")

                    # Check if the metadata has weights - if not, try to assign them
                    if not first_meta.weights:
                        self.stdout.write("    Metadata exists but no weights assigned, attempting to add weights...")
                        weights_file = self._find_model_weights_file()
                        if weights_file:
                            from pathlib import Path

                            from endoreg_db.utils.paths import STORAGE_DIR

                            try:
                                weights_path = str(Path(weights_file).relative_to(STORAGE_DIR))
                            except ValueError:
                                # Copy weights to storage if not already there
                                import shutil

                                weights_dir = STORAGE_DIR / "model_weights"
                                weights_dir.mkdir(parents=True, exist_ok=True)
                                dest_path = weights_dir / Path(weights_file).name
                                shutil.copy2(weights_file, dest_path)
                                weights_path = str(dest_path.relative_to(STORAGE_DIR))
                                self.stdout.write(f"      Copied weights to: {dest_path}")

                            # Assign the relative path to the FileField
                            first_meta.weights.name = weights_path
                            first_meta.save(update_fields=["weights"])
                            self.stdout.write(f"      Added weights to existing metadata: {weights_path}")

                    model.active_meta = first_meta
                    model.save()
                    fixed_count += 1
                    self.stdout.write(f"  ✅ Set active metadata: {first_meta.name} v{first_meta.version}")
                else:
                    self.stdout.write(f"  ⚠️  No metadata versions available for {model.name}")

            else:
                self.stdout.write(f"  ✅ Model {model.name} has active metadata: {model.active_meta}")

        # Verify all models can get latest version
        self.stdout.write("\nTesting model metadata access...")
        for model in all_models:
            try:
                latest = model.get_latest_version()
                self.stdout.write(f"  ✅ {model.name}: {latest}")
            except Exception as e:
                self.stdout.write(f"  ❌ {model.name}: {e}")
                raise Exception(f"Model {model.name} still has metadata issues: {e}")

        if fixed_count > 0:
            self.stdout.write(f"Fixed metadata for {fixed_count} model(s)")
        else:
            self.stdout.write("All models already had proper metadata")
