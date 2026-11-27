# Developer examples

This page collects the interactive Django shell examples formerly embedded in
the repository README. Run the shell after configuring Django:

```bash
uv run python manage.py shell
```

## Inspect a patient's examinations

```python
from endoreg_db.models import Patient

patient = Patient.objects.get(pk=1)
for examination in patient.examinations.all():
    print(examination)
```

The related manager name depends on the current model relationship. Inspect the
model or use Django shell completion when adapting this example.

## Inspect an examination indication and its interventions

```python
from endoreg_db.models import ExaminationIndication

indication = ExaminationIndication.objects.get(name="colonoscopy_screening")
for intervention in indication.expected_interventions.all():
    print(intervention)
```

This shows how a terminology definition can expose related expected
interventions. Relationship names and seed values are defined by the current
models and loaded data.

## Inspect video labels and annotated segments

```python
from endoreg_db.models import VideoFile

video = VideoFile.objects.first()
if video is not None:
    print("Video:", video.pk)
    print("Frame directory:", video.frame_dir)
```

To inspect available labels and segment relationships, follow the
[frame annotation support guide](wiki/frame_annotation_current_support.md).
Never use unscoped shell queries to access clinical data outside an authorized
development dataset.

## Inspect classification choices

```python
from endoreg_db.models import FindingClassification

classification = FindingClassification.objects.get(name="morphology")
print(classification)
```

Follow the classification's relations in the model definitions to list its
types and choices. The previous README screenshots are retained in
[`Images/`](../Images/) as historical illustrations; they may reflect data or
model versions that have since changed.

## More detail

- [Model layer map](model_layer_map_for_agents.md)
- [Case graph persistence](case_graph_persistence.md)
- [Dataloader YAML authoring](wiki/dataloader_yaml_authoring.md)
