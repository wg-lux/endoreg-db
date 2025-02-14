# django command to delete all LegacyImage objects

from django.core.management.base import BaseCommand, CommandError
from endoreg_db.models import LegacyImage
from tqdm import tqdm

class Command(BaseCommand):
    help = 'Deletes all LegacyImage objects including the linked files (legacy_image.image)'

    def handle(self, *args, **options):
        legacy_images = LegacyImage.objects.all()

        for legacy_image in tqdm(legacy_images):
            legacy_image.image.delete()
            legacy_image.delete()

        self.stdout.write(self.style.SUCCESS('Successfully deleted all LegacyImage objects and linked image files'))


