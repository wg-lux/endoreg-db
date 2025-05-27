import json
from django.apps import apps
from django.db.models import (
    CharField, TextField, EmailField, SlugField,
    IntegerField, FloatField, DecimalField,
    DateField, DateTimeField, BooleanField,
    ForeignKey, UUIDField, JSONField,
)

# Config
input_file = "endoreg_db_backup.json"
output_file = "endoreg_db_backup_fixed.json"

# Placeholders for required fields
PLACEHOLDERS = {
    CharField: "undefined",
    TextField: "n/a",
    EmailField: "unknown@example.com",
    SlugField: "undefined",
    IntegerField: 0,
    FloatField: 0.0,
    DecimalField: "0.0",
    DateField: None,
    DateTimeField: None,
    BooleanField: False, #need to change as it make issue
    ForeignKey: None,
    UUIDField: "00000000-0000-0000-0000-000000000000",
    JSONField: {},
}

ALL_NUMERIC_FIELDS = (IntegerField, FloatField, DecimalField)
ALL_BAD_FOR_EMPTY = ALL_NUMERIC_FIELDS + (ForeignKey,)

# Track what was fixed
fix_log = {}
total_fixes = 0


print("\n This fixture cleaner resolves issues caused by:")
print("   - Legacy or manually entered database records")
print("   - Required fields left as empty strings (\"\"), which break Django’s loaddata")
print("   - Improper values for foreign keys or numeric fields")
print("   - Common in migrations from older systems or bulk inserts\n")

print(" Placeholder values used for missing data might inlcude:")
for field_type, value in PLACEHOLDERS.items():
    print(f"  {field_type.__name__:<15}: {repr(value)}")

print("") 

def get_required_fields(model):
    """Get all required fields (null=False) and their default placeholders."""
    required_fields = {}
    for field in model._meta.get_fields():
        if not getattr(field, 'null', True):
            # Skip M2M and auto fields
            if field.many_to_many or not hasattr(field, 'get_internal_type'):
                continue
            for field_type, placeholder in PLACEHOLDERS.items():
                if isinstance(field, field_type):
                    required_fields[field.name] = placeholder
                    break
    return required_fields

def fix_fields(obj):
    global total_fixes
    model_label = obj["model"]
    app_label, model_name = model_label.split(".")
    model = apps.get_model(app_label, model_name)

    required_fields = get_required_fields(model)
    fields = obj.get("fields", {})

    for field in model._meta.get_fields():
        key = field.name
        if key not in fields:
            continue
        value = fields.get(key)

        # Fix "" in numeric/FK fields regardless of required/optional
        if value == "" and isinstance(field, ALL_BAD_FOR_EMPTY):
            fields[key] = None
            fix_log.setdefault(model_label, []).append(key)
            total_fixes += 1

        # Fix "" in required text fields
        elif value == "" and key in required_fields:
            fields[key] = required_fields[key]
            fix_log.setdefault(model_label, []).append(key)
            total_fixes += 1

    return obj

# Load original JSON
with open(input_file, "r") as f:
    data = json.load(f)

# Check if fixing is needed
needs_fixing = any(
    obj.get("fields", {}).get(key) == ""
    for obj in data
    for key in obj.get("fields", {})
)

if not needs_fixing:
    print(" No empty fields found. Fixture is already clean.")
else:
    print(" Fixing empty values in fixture...")
    fixed_data = [fix_fields(obj) for obj in data]

    with open(output_file, "w") as f:
        json.dump(fixed_data, f, indent=4)

    print(f" Cleaned fixture written to: {output_file}")
    print(f" Total fields fixed: {total_fixes}")
    print(" Breakdown by model:")

    for model, fields in fix_log.items():
        unique_fields = set(fields)
        print(f"  - {model}: {len(fields)} changes in fields: {', '.join(unique_fields)}")



