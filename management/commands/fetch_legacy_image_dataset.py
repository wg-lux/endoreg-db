# command to fetch the legacy dataset and save it to a json file.
from django.core.management.base import BaseCommand, CommandError
from endoreg_db.queries import generate_legacy_dataset_output 
import json

LABELSET_NAME = "multilabel_classification"
LABELSET_VERSION = 0

class Command(BaseCommand):
    help = 'Fetches the legacy dataset and saves it to a jsonl file'

    def handle(self, *args, **options):
        output, labelset = generate_legacy_dataset_output(LABELSET_NAME, LABELSET_VERSION)
        # output is a list of dicts, each dict is an image
        labels_in_order = labelset.get_labels_in_order()

        # write to file
        with open('legacy_dataset.jsonl', 'w') as f:
            for img_dict in output:
                f.write(json.dumps(img_dict) + '\n')


        _dict = {
            "labels": [label.name for label in labels_in_order],
            "name": LABELSET_NAME,
            "version": LABELSET_VERSION
        }

        with open('legacy_dataset_info.json', 'w') as f:
            f.write(json.dumps(_dict))

        self.stdout.write(self.style.SUCCESS('Successfully fetched and saved the legacy dataset'))
