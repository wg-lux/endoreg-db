# Endoreg Db Models Contribution Guide

For endoreg-db models, the data is defined in YAML files. This data is imported using to keep a consistent and human-readable format, that allows medical personnel to add data to the database by editing YAML files. The goal is to keep the barrier to entry low and to enable co usability.

The data is loaded by using the command:

```shell
python manage.py load_base_db_data
```

While this approach ensures easy import of data, it introduces some constraints on the Django model architecture.

## Natural Keys

A function get_by_natural_key is to be added for each new model. This allows the data loader to utilize the name instead of the ID to store a models relations in the database.

This also allows to retrieve the data for a given model using the name field as well as the ID.


```python
class ExaminationManager(models.Manager):
    """
    Manager for Examination with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "Examination":
        return self.get(name=name)
```

# Through Models & YAML – Correct, Loader-Compatible Pattern

## Why an explicit through model here?

We want **named, queryable link groups with metadata** (e.g., `enabled_by_default`) between *Examinations* and *RequirementSets*. Instead of a hidden Django auto-table, we define a **first-class model**:

* `ExaminationRequirementSet` (ERS) — *a named group* of one or more `Examination`s, with optional per-group metadata.
* Each `RequirementSet` can link to one or more of these ERS groups via `reqset_exam_links`.

> This is intentionally not a “classic” `through=…` join between `Examination` and `RequirementSet`. It’s a small hub entity:
>
> ```
> Examination  —M2M→  ExaminationRequirementSet  ←M2M—  RequirementSet
> ```

This shape gives us:

* **Stable natural keys** (`name`) for YAML import.
* **Metadata on the link group** (`enabled_by_default`, future fields).
* **Reusability**: Many `RequirementSet`s can reference the same ERS group.

---

## Model definitions (authoritative)

### Examination (natural key support)

```python
class ExaminationManager(models.Manager):
    def get_by_natural_key(self, name: str) -> "Examination":
        return self.get(name=name)

class Examination(models.Model):
    name = models.CharField(max_length=100, unique=True)
    examination_types = models.ManyToManyField("ExaminationType", blank=True)
    description = models.TextField(blank=True, null=True)

    objects = ExaminationManager()

    def natural_key(self) -> tuple:
        return (self.name,)
```

### RequirementSetType (natural key support)

```python
class RequirementSetTypeManager(models.Manager):
    def get_by_natural_key(self, name: str) -> "RequirementSetType":
        return self.get(name=name)

class RequirementSetType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = RequirementSetTypeManager()

    def natural_key(self):
        return (self.name,)
```

### ExaminationRequirementSet (ERS) — the “through” hub

```python
class ExaminationRequirementSetManager(models.Manager):
    def get_by_natural_key(self, name: str) -> "ExaminationRequirementSet":
        return self.get(name=name)

class ExaminationRequirementSet(models.Model):
    """
    Named link-group of one or more Examinations.
    Referenced from RequirementSet via reqset_exam_links.
    """
    name = models.CharField(max_length=100, unique=True)

    # Link to the examinations this group applies to
    examinations = models.ManyToManyField(
        "Examination",
        related_name="exam_reqset_links",
        blank=True,
    )

    # Per-group metadata (extend freely later)
    enabled_by_default = models.BooleanField(default=False)

    objects = ExaminationRequirementSetManager()

    def natural_key(self) -> tuple:
        return (self.name,)
```

### RequirementSet (links to ERS via `reqset_exam_links`)

```python
class RequirementSetManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class RequirementSet(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    requirements = models.ManyToManyField(
        "Requirement", blank=True, related_name="requirement_sets",
    )
    links_to_sets = models.ManyToManyField(
        "RequirementSet", blank=True, related_name="links_from_sets",
    )
    requirement_set_type = models.ForeignKey(
        "RequirementSetType", on_delete=models.CASCADE,
        related_name="requirement_sets", blank=True, null=True,
    )
    information_sources = models.ManyToManyField(
        "InformationSource", related_name="requirement_sets", blank=True,
    )
    # <-- key line: RequirementSet links to ERS groups here
    reqset_exam_links = models.ManyToManyField(
        "ExaminationRequirementSet", related_name="requirement_set", blank=True,
    )
    tags = models.ManyToManyField("Tag", related_name="requirement_sets", blank=True)

    objects = RequirementSetManager()

    def natural_key(self):
        return (self.name,)
```

> ✅ **Note:** There is no direct M2M between `Examination` and `RequirementSet`. All linking goes through ERS.

---

## YAML schema (exact)

### 1) `examination_requirement_set/data.yaml` (create ERS groups first)

* **Key**: `model: endoreg_db.examination_requirement_set`
* **Natural key**: `fields.name`
* **M2M**: `fields.examinations` (list of Examination names)

```yaml
- model: endoreg_db.examination_requirement_set
  fields:
    name: "colonoscopy_req_sets"
    examinations: ["colonoscopy"]

- model: endoreg_db.examination_requirement_set
  fields:
    name: "colonoscopy_austria_req_sets"
    examinations: ["colonoscopy_austria_screening"]
```

Optional metadata:

```yaml
- model: endoreg_db.examination_requirement_set
  fields:
    name: "colonoscopy_req_sets_default_on"
    examinations: ["colonoscopy"]
    enabled_by_default: true
```

### 2) `requirement_set/data.yaml` (reference ERS by name via `reqset_exam_links`)

* **Key**: `models: endoreg_db.requirement_set`
* **Natural key**: `fields.name`
* **FKs/M2M**:

  * `requirement_set_type`: name of `RequirementSetType`
  * `requirements`: list of `Requirement` names
  * `links_to_sets`: list of `RequirementSet` names
  * `information_sources`: list of `InformationSource` names
  * `tags`: list of `Tag` names
  * `reqset_exam_links`: list of **ERS names** (critical)

```yaml
- models: endoreg_db.requirement_set
  fields:
    name: "colonoscopy_austria_screening_qa"
    name_de: "Vorsorge Koloskopie Österreich QA"
    name_en: "Colonoscopy Austria Screening QA"
    description: "Colonoscopy Austria Screening QA"
    requirement_set_type: "all"
    links_to_sets:
      - "colonoscopy_austria_screening_finding_polyp_required_classifications"
      - "colonoscopy_austria_screening_required_patient_information"
      - "colonoscopy_austria_screening_examination_required_findings"
    tags:
      - "report_mask_requirement_set"
      - "colonoscopy_austria_screening"
    reqset_exam_links:
      - "colonoscopy_austria_req_sets"   # <-- ERS name
      - "colonoscopy_req_sets"           # <-- ERS name
```

### 3) `examinations/data.yaml` (for completeness)

* **Key**: `model: endoreg_db.Examination`
* **Natural key**: `fields.name`
* **M2M**: `examination_types` (list)

---

## Import order (critical)

The management command already enforces a correct order via `IMPORT_MODELS`:

1. `RequirementType`
2. `RequirementOperator`
3. `Requirement`
4. `RequirementSetType`
5. **`ExaminationRequirementSet`**  ⟵ ERS must exist before RequirementSet
6. `RequirementSet`

> The dataloader (`load_model_data_from_yaml` → `load_data_with_foreign_keys`) calls
> `fk_model.objects.get_by_natural_key(...)` for every FK/M2M.
> If you try to load `RequirementSet` **before** the referenced `ExaminationRequirementSet` objects exist, the loader will warn and skip those unresolved relations.

---

## How the loader resolves these YAMLs (unchanged code)

* For each entry:

  * Pops `name` (natural key).
  * Resolves every field listed in `foreign_keys` by its **name** via `get_by_natural_key`.
  * If a field’s value is a **list**, it’s treated as **M2M** and stored in `m2m_relationships`.
  * Performs `update_or_create(defaults=fields, name=name)` on the main model.
  * Applies **M2M sets** after save: `obj.<m2m_field>.set(related_objs)`.

In our setup:

* ERS import:

  * Model: `ExaminationRequirementSet`
  * `foreign_keys = ["examinations"]`
  * Loader resolves `examinations: ["colonoscopy", ...]` → actual `Examination` instances → `ers.examinations.set(...)`.

* RequirementSet import:

  * Model: `RequirementSet`
  * `foreign_keys` includes: `requirement_set_type`, `requirements`, `links_to_sets`, `information_sources`, `tags`, **`reqset_exam_links`**
  * Loader resolves `reqset_exam_links: ["colonoscopy_austria_req_sets", ...]` → actual `ExaminationRequirementSet` instances → `rs.reqset_exam_links.set(...)`.

No code changes to the loader are necessary.

---

## Querying the links (examples)

```python
# All ERS groups an RS belongs to:
rs = RequirementSet.objects.get(name="colonoscopy_austria_screening_qa")
list(rs.reqset_exam_links.values_list("name", flat=True))
# → ["colonoscopy_austria_req_sets", "colonoscopy_req_sets"]

# All Examinations covered by those groups (unique):
from django.db.models import Prefetch
exams = (
    Examination.objects.filter(exam_reqset_links__requirement_set=rs)
    .distinct()
)
# or via ERS hop:
exams2 = Examination.objects.filter(exam_reqset_links__in=rs.reqset_exam_links.all()).distinct()

# All RequirementSets that apply to a given Examination:
col = Examination.objects.get(name="colonoscopy")
rs_qs = RequirementSet.objects.filter(reqset_exam_links__examinations=col).distinct()

# Access metadata on the ERS group:
for ers in rs.reqset_exam_links.all():
    print(ers.name, ers.enabled_by_default)
```

---

## Adding metadata to the link group

Add fields to `ExaminationRequirementSet` (e.g., `priority = models.PositiveIntegerField(default=100)`) and expose them in YAML:

```yaml
- model: endoreg_db.examination_requirement_set
  fields:
    name: "colonoscopy_req_sets"
    examinations: ["colonoscopy"]
    enabled_by_default: true
    priority: 50
```

Run migrations; the loader will pick up the new scalar field (because it is part of `fields` and not an FK/M2M). No loader change required.

---

## Common pitfalls (and exact fixes)

1. **ERS not imported yet**

   * Symptom: warnings for unresolved `reqset_exam_links`.
   * Fix: ensure `ExaminationRequirementSet` YAML is in place and imported **before** `RequirementSet` (the provided `IMPORT_MODELS` order already does that).

2. **Missing `get_by_natural_key`**

   * Symptom: `AttributeError: 'Manager' object has no attribute 'get_by_natural_key'`.
   * Fix: ensure every FK/M2M target used by the loader (e.g., `Examination`, `RequirementSetType`, `ExaminationRequirementSet`, etc.) implements

     ```python
     def get_by_natural_key(self, name: str): return self.get(name=name)
     ```

3. **Non-unique names**

   * Symptom: `MultipleObjectsReturned` during import.
   * Fix: maintain `unique=True` on all `name` fields used as natural keys.

4. **Accidental deletions not re-applied**

   * The loader uses `.set(...)` for M2M, which is **authoritative** per file run. If you manually modify links in the DB and rerun the import, the YAML wins.

---

## Minimal smoke test

```python
def test_reqset_ers_resolution(db):
    ers = ExaminationRequirementSet.objects.get(name="colonoscopy_austria_req_sets")
    assert ers.examinations.filter(name="colonoscopy_austria_screening").exists()

    rs = RequirementSet.objects.get(name="colonoscopy_austria_screening_qa")
    assert rs.reqset_exam_links.filter(name="colonoscopy_austria_req_sets").exists()

    # round-trip: which RS apply to a given exam?
    exam = Examination.objects.get(name="colonoscopy_austria_screening")
    rs_for_exam = RequirementSet.objects.filter(reqset_exam_links__examinations=exam).distinct()
    assert rs in rs_for_exam
```

---

## TL;DR – Authoring checklist

* Give every model referenced by name a `get_by_natural_key(name)` and unique `name`.
* Define **ERS** (`ExaminationRequirementSet`) with:

  * `name` (unique, natural key)
  * `examinations` (M2M to `Examination`)
  * any metadata (`enabled_by_default`, etc.)
* Reference ERS from `RequirementSet` via `reqset_exam_links` (list of ERS names).
* Keep the import order where **ERS is loaded before RequirementSet**.
* The provided dataloader already resolves all of this—no changes required.
