"""
Django management command to perform complete setup for EndoReg DB when used as an embedded app.
This command ensures all necessary data and configurations are initialized.
"""

import os
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand
from endoreg_db.models import ModelMeta

class Command(BaseCommand):
    help = """
    Complete setup for EndoReg DB when used as an embedded app.
    This command performs all necessary initialization steps:
    1. Loads base database data
    2. Sets up AI models and labels
    3. Creates cache table
    4. Initializes model metadata
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

    def handle(self, *args, **options):
        skip_ai = options.get("skip_ai_setup", False)
        force_recreate = options.get("force_recreate", False)

        self.stdout.write(self.style.SUCCESS("🚀 Starting EndoReg DB embedded app setup..."))

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
                # Check if model metadata already exists
                from endoreg_db.models import AiModel

                default_model_name = "image_multilabel_classification_colonoscopy_default"
                ai_model = AiModel.objects.filter(name=default_model_name).first()

                if not ai_model:
                    self.stdout.write(self.style.ERROR(f"❌ AI model '{default_model_name}' not found"))
                    return

                existing_meta = ai_model.metadata_versions.first()
                if existing_meta and not force_recreate:
                    self.stdout.write(self.style.SUCCESS("✅ Model metadata already exists (use --force-recreate to recreate)"))
                else:
                    # Try to create model metadata
                    model_path = self._find_model_weights_file()
                    if model_path:
                        call_command(
                            "create_multilabel_model_meta",
                            model_name=default_model_name,
                            model_meta_version=1,
                            image_classification_labelset_name="multilabel_classification_colonoscopy_default",
                            model_path=str(model_path),
                        )
                        self.stdout.write(self.style.SUCCESS("✅ AI model metadata created successfully"))
                    else:
                        self.stdout.write(self.style.WARNING("⚠️  Model weights file not found. AI features may not work properly."))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed to create AI model metadata: {e}"))
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
        self.stdout.write("1. Run migrations: python manage.py migrate")
        self.stdout.write("2. Create superuser: python manage.py createsuperuser")
        self.stdout.write("3. Start development server: python manage.py runserver")

    def _find_model_weights_file(self):
        """Find the model weights file in various possible locations."""
        # Check common locations for model weights
        
        if not ModelMeta.objects.exists():
            print("📦 No model metadata found — creating from Hugging Face...")
            ModelMeta.setup_default_from_huggingface(
                "wg-lux/colo_segmentation_RegNetX800MF_base",
                labelset_name="multilabel_classification_colonoscopy_default"
            )
            print("✅ Default ModelMeta created.")
        possible_paths = [
            # Test assets (for development)
            Path("tests/assets/colo_segmentation_RegNetX800MF_6.ckpt"),
            # Project root assets
            Path("assets/colo_segmentation_RegNetX800MF_6.ckpt"),
            # Storage directory
            Path("data/storage/model_weights/colo_segmentation_RegNetX800MF_6.ckpt"),
            # Absolute paths based on environment
            Path(os.getenv("STORAGE_DIR", "storage")) / "model_weights" / "colo_segmentation_RegNetX800MF_6.ckpt",
        ]

        for path in possible_paths:
            if path.exists():
                self.stdout.write(f"Found model weights at: {path}")
                return path

        self.stdout.write("Model weights file not found in standard locations")
        
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
        

