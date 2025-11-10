# Tag Management Guide

## Übersicht

Tags werden in der EndoReg-DB verwendet, um Requirement Sets zu kategorisieren und zu filtern. Tags können über YAML-Konfigurationsdateien verwaltet werden.

## Tags hinzufügen

### Schritt 1: YAML-Datei bearbeiten

Öffne die Datei: `endoreg_db/data/tag/requirement_set_tags.yaml`

Füge einen neuen Tag-Eintrag hinzu:

```yaml
- model: endoreg_db.tag
  fields:
    name: "Dein Tag Name"
```

### Schritt 2: Tags in Datenbank laden

Führe den Management-Command aus:

```bash
uv run python manage.py load_tag_data --verbose
```

Dieser Command:
- Liest alle `.yaml` Dateien aus `endoreg_db/data/tag/`
- Erstellt neue Tags oder aktualisiert bestehende (basierend auf `name` als unique identifier)
- Zeigt bei `--verbose` detaillierte Output-Informationen

### Beispiel: Neuen "Nurse" Tag hinzufügen

**Vorher** (`requirement_set_tags.yaml`):
```yaml
- model: endoreg_db.tag
  fields:
    name: "Gastroenterologist"

- model: endoreg_db.tag
  fields:
    name: "Student"
```

**Nachher** (`requirement_set_tags.yaml`):
```yaml
- model: endoreg_db.tag
  fields:
    name: "Gastroenterologist"

- model: endoreg_db.tag
  fields:
    name: "Student"

- model: endoreg_db.tag
  fields:
    name: "Nurse"  # <-- NEU
```

**Dann ausführen:**
```bash
uv run python manage.py load_tag_data --verbose
```

**Output:**
```
Start loading Tag
1  # Anzahl der geladenen YAML-Dateien
```

## Tags verwenden

### 1. Tags einem RequirementSet zuweisen

#### Via Django Admin Interface:
1. Navigiere zu "Requirement Sets"
2. Öffne ein RequirementSet
3. Wähle Tags aus dem "Tags" Feld
4. Speichern

#### Via Django Shell:
```python
from endoreg_db.models import RequirementSet, Tag

# Tag abrufen
gastro_tag = Tag.objects.get(name="Gastroenterologist")

# RequirementSet abrufen
req_set = RequirementSet.objects.get(name="Advanced Colonoscopy QA")

# Tags zuweisen
req_set.tags.add(gastro_tag)  # Einen Tag hinzufügen
req_set.tags.set([gastro_tag, another_tag])  # Alle Tags ersetzen
req_set.tags.remove(gastro_tag)  # Einen Tag entfernen
```

### 2. RequirementSets nach Tags filtern

#### Via API:
```bash
curl -X POST http://localhost:8000/api/lookup/init/ \
  -H "Content-Type: application/json" \
  -d '{
    "patient_examination_id": 123,
    "user_tags": ["Gastroenterologist", "Professor"]
  }'
```

#### Via Python Service:
```python
from endoreg_db.services import lookup_service as ls
from endoreg_db.models import PatientExamination

pe = PatientExamination.objects.get(pk=123)

# Ohne Filter - alle RequirementSets
all_sets = ls.requirement_sets_for_patient_exam(pe)

# Mit Filter - nur RequirementSets mit diesen Tags
filtered_sets = ls.requirement_sets_for_patient_exam(
    pe, 
    user_tags=["Gastroenterologist", "Student"]
)
```

## Vorhandene Tags anzeigen

### Via Django Shell:
```python
from endoreg_db.models import Tag

# Alle Tags auflisten
tags = Tag.objects.all().order_by('name')
for tag in tags:
    print(f"- {tag.name}")

# Tag-Anzahl
print(f"Total: {Tag.objects.count()} tags")
```

### Via Management Command:
```bash
uv run python manage.py shell -c "
from endoreg_db.models import Tag
tags = Tag.objects.all().order_by('name')
print(f'Total tags: {tags.count()}')
for tag in tags:
    print(f'  - {tag.name}')
"
```

## Tag-Modell Schema

```python
class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    
    # Reverse Relationship
    requirement_sets: ManyToManyField[RequirementSet]
```

**Felder:**
- `name`: Eindeutiger Name des Tags (max. 100 Zeichen)

**Beziehungen:**
- `requirement_sets`: Many-to-Many Beziehung zu RequirementSet

## Best Practices

### Tag-Namenskonventionen:
- **Verwende sprechende Namen**: "Gastroenterologist" statt "gastro"
- **CamelCase für Rollen**: "Terminology Expert" statt "terminology_expert"
- **snake_case für technische Tags**: "report_mask_requirement_set"
- **Keine Duplikate**: Der `name` ist UNIQUE

### Tag-Organisation:
- Gruppiere verwandte Tags mit Kommentaren in der YAML:
  ```yaml
  # User role tags
  - model: endoreg_db.tag
    fields:
      name: "Gastroenterologist"
  
  # Technical/system tags  
  - model: endoreg_db.tag
    fields:
      name: "report_mask_requirement_set"
  ```

### Testen:
Nach dem Hinzufügen neuer Tags:
```bash
# 1. Tags laden
uv run python manage.py load_tag_data --verbose

# 2. Überprüfen ob Tags existieren
uv run python manage.py shell -c "
from endoreg_db.models import Tag
print(Tag.objects.filter(name='Dein Neuer Tag').exists())
"

# 3. Tests ausführen
uv run pytest tests/services/test_lookup_tag_filtering.py -v
```

## Troubleshooting

### Problem: Tag wird nicht geladen
**Lösung**: 
- Überprüfe YAML-Syntax (Einrückung!)
- Stelle sicher dass die Datei `.yaml` Endung hat
- Überprüfe ob die Datei in `endoreg_db/data/tag/` liegt

### Problem: "UNIQUE constraint failed"
**Lösung**: 
- Ein Tag mit diesem Namen existiert bereits
- Überprüfe vorhandene Tags: `Tag.objects.filter(name='...')`
- Der `load_tag_data` Command aktualisiert bestehende Tags basierend auf `name`

### Problem: Tags funktionieren nicht beim Filtern
**Lösung**:
- Überprüfe ob Tags dem RequirementSet zugewiesen sind: `req_set.tags.all()`
- Überprüfe ob Tag-Namen exakt übereinstimmen (case-sensitive)
- Überprüfe ob die RequirementSets mit dem Examination verknüpft sind

## Verwandte Dateien

- **Tag Model**: `endoreg_db/models/other/tag.py`
- **Tag YAML Data**: `endoreg_db/data/tag/requirement_set_tags.yaml`
- **Load Command**: `endoreg_db/management/commands/load_tag_data.py`
- **Lookup Service**: `endoreg_db/services/lookup_service.py`
- **Tests**: `tests/services/test_lookup_tag_filtering.py`
- **API View**: `endoreg_db/views/requirement/lookup.py`
