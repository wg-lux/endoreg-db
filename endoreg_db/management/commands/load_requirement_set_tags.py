"""
Management command to load default requirement set tags for role-based filtering.

⚠️ DEPRECATED: This command is deprecated. Please use 'load_tag_data' instead.

The new method loads tags from YAML configuration files, making it easier to
add, modify, or remove tags without changing Python code.

New Usage:
    uv run python manage.py load_tag_data --verbose

Tags are now managed in: endoreg_db/data/tag/requirement_set_tags.yaml

This command is kept for backward compatibility but may be removed in future versions.
"""

import logging

from django.core.management.base import BaseCommand

from endoreg_db.models import Tag

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    ⚠️ DEPRECATED: Load default requirement set tags for role-based filtering.

    Please use 'load_tag_data' instead:
        uv run python manage.py load_tag_data --verbose

    This command creates or verifies the existence of default tags used
    for filtering requirement sets by user role/expertise level.
    """

    help = "Load default requirement set tags for role-based filtering"

    def handle(self, *args, **options):
        """
        Create or verify default tags for requirement set filtering.

        Tags created:
            - Gastroenterologist: For specialist-level requirements
            - Student: For educational/basic requirements
            - Professor: For academic/research requirements
            - Terminology Expert: For terminology standardization
            - Default Prototype: For baseline/reference requirements
        """
        default_tags = [
            ("Gastroenterologist", "Specialist-level endoscopy requirements"),
            ("Student", "Educational and basic learning requirements"),
            ("Professor", "Academic and research-focused requirements"),
            ("Terminology Expert", "Terminology standardization requirements"),
            ("Default Prototype", "Baseline reference requirements"),
        ]

        created_count = 0
        updated_count = 0

        for tag_name, description in default_tags:
            tag, created = Tag.objects.get_or_create(
                name=tag_name,
                defaults={"description": description}
                if hasattr(Tag, "description")
                else {},
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"✓ Created tag: {tag_name}"))
                logger.info(f"Created requirement set tag: {tag_name}")
            else:
                updated_count += 1
                self.stdout.write(f"  Tag already exists: {tag_name}")

        # Summary
        total_tags = len(default_tags)
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'=' * 60}\n"
                f"Tag Loading Complete\n"
                f"{'=' * 60}\n"
                f"  New tags created:     {created_count}\n"
                f"  Existing tags found:  {updated_count}\n"
                f"  Total tags verified:  {total_tags}\n"
                f"{'=' * 60}"
            )
        )

        logger.info(
            f"Requirement set tag loading complete: "
            f"{created_count} created, {updated_count} existing, "
            f"{total_tags} total"
        )
