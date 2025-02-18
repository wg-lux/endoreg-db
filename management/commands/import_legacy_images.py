# command expects a directory as an argument
# directory should contain a img_dicts.jsonl file, a settings.json and an "images" directory
# images should be in jpeg format
# reads the img_dicts.jsonl file and creates a new database entry for each image
# img_dicts contains a line for each image in the following format:
# {"id": 30604292, "path": "images/30604292.jpg", "labels": ["appendix", "polyp"]}

# import_settings.json example:
# {
#     "annotator": "legacy",
#     "labelset_name": "multilabel_classification",
#     "labelset_version": 0
# }

import os
import json
from tqdm import tqdm
from django.core.management.base import BaseCommand
from endoreg_db.models import LegacyImage, LabelSet, Label, ImageClassificationAnnotation
from django.core.files import File
from uuid import uuid4

class Command(BaseCommand):
    """
    Imports images from a directory into the database

    Usage:
        python manage.py import_legacy_images <directory>
    """
    help = 'Imports images from a directory into the database'

    def add_arguments(self, parser):
        parser.add_argument('directory', type=str)

    def handle(self, *args, **options):
        directory = options['directory']
        if not os.path.isdir(directory):
            raise Exception(f"Directory {directory} does not exist")

        # read settings.json
        with open(os.path.join(directory, 'import_settings.json'), 'r') as f:
            settings = json.load(f)

        # read img_dicts.jsonl
        with open(os.path.join(directory, 'img_dicts.jsonl'), 'r') as f:
            img_dicts = [json.loads(line) for line in f.readlines()]

        # get labelset
        labelset_name = settings['labelset_name']
        labelset_version = settings['labelset_version']
        try:
            labelset = LabelSet.objects.get(name=labelset_name, version=labelset_version)
        except LabelSet.DoesNotExist:
            raise Exception(f"No labelset found with the name {labelset_name} and version {labelset_version}")


        # get labels in dict to lookup by name
        labels = {label.name: label for label in labelset.labels.all()}

        # create images and image_classification_annotations
        for img_dict in tqdm(img_dicts):

            # Open the image file
            with open(os.path.join(directory, img_dict['path']), 'rb') as f:
                # Create the Django File object
                django_file = File(f)
                
                # Extract only the filename
                filename = os.path.basename(img_dict['path'])
                img_suffix = os.path.splitext(filename)[1]
                # assign uuid as new filename
                filename = str(uuid4()) + img_suffix
                

                # Create a new LegacyImage instance and save the image
                image = LegacyImage(image=filename, suffix=img_suffix)
                image.image.save(filename, django_file, save=True)

                image_annotations = []
                for label_name, label in labels.items():
                    if label_name in img_dict['labels']:
                        value = True
                    else:
                        value = False

                    image_annotations.append(ImageClassificationAnnotation(
                        legacy_image=image,
                        label=label,
                        value=value,
                        annotator=settings['annotator']
                    ))

                ImageClassificationAnnotation.objects.bulk_create(image_annotations)

