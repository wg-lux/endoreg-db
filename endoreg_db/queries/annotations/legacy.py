from ...models import LabelSet, ImageClassificationAnnotation
from django.db.models import Q, F
from django.db import models
from tqdm import tqdm
from collections import defaultdict

# def get_legacy_annotations_for_labelset(labelset_name, version=None):
#     """
#     Retrieve annotations for a given label set for training.

#     Args:
#     - labelset_name (str): The name of the label set.
#     - version (int, optional): The version of the label set. If not specified, the latest version is fetched.

#     Returns:
#     - list[dict]: A list of dictionaries. Each dictionary represents an image and its annotations.
#                   Format: [{"frame": <frame_object>, "annotations": [{"label": <label_name>, "value": <value>}, ...]}, ...]

#     Example:
#         annotations_for_training = get_annotations_for_labelset("YourLabelSetName", version=2)

#     """

#     # Fetch the label set based on the name and optionally the version
#     if version:
#         labelset = LabelSet.objects.get(name=labelset_name, version=version)
#     else:
#         labelset = LabelSet.objects.filter(name=labelset_name).order_by('-version').first()
#         if not labelset:
#             raise ValueError(f"No label set found with the name: {labelset_name}")

#     # Retrieve all labels in the label set
#     labels_in_set = labelset.labels.all()

#     # Get the most recent annotations for each frame/label combination
#     annotations = ImageClassificationAnnotation.objects.filter(label__in=labels_in_set)
#     annotations = annotations.annotate(
#         latest_annotation=models.Window(
#             expression=models.functions.RowNumber(),
#             partition_by=[F('legacy_image'), F('label')],
#             order_by=F('date_modified').desc()
#         )
#     ).filter(latest_annotation=1)

#     # Organize the annotations by image/frame
#     organized_annotations = []

#     for annotation in tqdm(annotations):
#         # ic(annotation)
#         # Check if the frame is already in the organized list
#         existing_entry = next((entry for entry in organized_annotations if entry['legacy_image'] == annotation.legacy_frame), None)

#         if existing_entry:
#             # Add this annotation to the existing frame's annotations
#             existing_entry['annotations'].append({
#                 "label": annotation.label.name,
#                 "value": annotation.value
#             })
#         else:
#             # Create a new entry for this frame
#             organized_annotations.append({
#                 "legacy_image": annotation.legacy_image,
#                 "annotations": [{
#                     "label": annotation.label.name,
#                     "value": annotation.value
#                 }]
#             })

#     return organized_annotations



def get_legacy_annotations_for_labelset(labelset_name, version=None):
    """
    ... [rest of your docstring]
    """

    # Fetch the label set based on the name and optionally the version
    if version:
        labelset = LabelSet.objects.get(name=labelset_name, version=version)
    else:
        labelset = LabelSet.objects.filter(name=labelset_name).order_by('-version').first()
        if not labelset:
            raise ValueError(f"No label set found with the name: {labelset_name}")

    # Retrieve all labels in the label set
    labels_in_set = labelset.labels.all()

    # Get the most recent annotations for each frame/label combination
    annotations = (ImageClassificationAnnotation.objects
                   .filter(label__in=labels_in_set)
                   .select_related('legacy_image', 'label')  # Reduce number of queries
                   .annotate(
                        latest_annotation=models.Window(
                            expression=models.functions.RowNumber(),
                            partition_by=[F('legacy_image'), F('label')],
                            order_by=F('date_modified').desc()
                        )
                    ).filter(latest_annotation=1))

    # Organize the annotations by image/frame using a defaultdict
    organized_annotations_dict = defaultdict(lambda: {
        "legacy_image": None,
        "annotations": []
    })

    for annotation in tqdm(annotations):
        organized_entry = organized_annotations_dict[annotation.legacy_image.id]
        organized_entry["legacy_image"] = annotation.legacy_image
        organized_entry["annotations"].append({
            "label": annotation.label.name,
            "value": annotation.value
        })

    # Convert organized_annotations_dict to a list
    organized_annotations = list(organized_annotations_dict.values())

    return organized_annotations

def generate_legacy_dataset_output(labelset_name, version=None):
    """
    Generate an output suitable for creating PyTorch datasets.

    Args:
    - labelset_name (str): The name of the label set.
    - version (int, optional): The version of the label set. If not specified, the latest version is fetched.

    Returns:
    - list[dict]: A list of dictionaries, where each dictionary contains the file path and the labels.
                  Format: [{"path": <file_path>, "labels": [<label_1_value>, <label_2_value>, ...]}, ...]
    - labelset[LabelSet]: The label set that was used to generate the output.
    """

    # First, retrieve the organized annotations using the previously defined function
    organized_annotations = get_legacy_annotations_for_labelset(labelset_name, version)

    # Fetch all labels from the labelset for consistent ordering
    labelset = LabelSet.objects.get(name=labelset_name, version=version)
    all_labels = labelset.get_labels_in_order()

    dataset_output = []

    for entry in organized_annotations:
        # Prepare a dictionary for each frame
        frame_data = {
            "path": entry['legacy_image'].image.path,  # Assuming 'image' field stores the file path
            "labels": [-1] * len(all_labels)  # Initialize with -1 for all labels
        }

        # Update the labels based on the annotations
        for annotation in entry['annotations']:
            index = next((i for i, label in enumerate(all_labels) if label.name == annotation['label']), None)
            if index is not None:
                frame_data['labels'][index] = int(annotation['value'])

        dataset_output.append(frame_data)

    return dataset_output, labelset
