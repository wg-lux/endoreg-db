from django.test import TestCase
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from pathlib import Path
from endoreg_db.models import Center, FirstName, LastName


class CenterModelTest(TestCase):
    def test_center_key_is_globally_unique(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Center.objects.bulk_create(
                [
                    Center(name="first", center_key="duplicate"),
                    Center(name="second", center_key="duplicate"),
                ]
            )

    def test_canonical_graph_has_one_declared_leaf(self) -> None:
        loader = MigrationLoader(connection)
        manifest = (
            Path(__file__).resolve().parents[3]
            / "endoreg_db/migrations/max_migration.txt"
        )
        self.assertEqual(
            loader.graph.leaf_nodes("endoreg_db"),
            [("endoreg_db", manifest.read_text().strip())],
        )
        self.assertEqual(loader.detect_conflicts(), {})

    def test_canonical_model_state_needs_no_migration(self) -> None:
        call_command(
            "makemigrations", "endoreg_db", check=True, dry_run=True, interactive=False
        )

    def setUp(self):
        # Create a Center instance for testing
        self.center = Center.objects.create(
            name="test_center",
        )

    def test_load_center_data_command(self):
        """Test if the load_center command runs without errors."""
        try:
            call_command("load_center_data")
        except Exception as e:
            self.fail(f"load_center_data command failed: {e}")

        # get Würzburg
        center: Center = Center.objects.get(name="university_hospital_wuerzburg")
        fn = [str(_.name).lower() for _ in center.first_names.all()]
        ln = [str(_.name).lower() for _ in center.last_names.all()]
        self.assertEqual(fn, [])
        self.assertEqual(ln, [])

    def test_reload_preserves_locally_imported_names(self):
        call_command("load_center_data")
        center = Center.objects.get(name="university_hospital_wuerzburg")
        first = FirstName.objects.create(name="Local")
        last = LastName.objects.create(name="Employee")
        center.first_names.add(first)
        center.last_names.add(last)
        call_command("load_center_data")
        self.assertEqual(list(center.first_names.all()), [first])
        self.assertEqual(list(center.last_names.all()), [last])
