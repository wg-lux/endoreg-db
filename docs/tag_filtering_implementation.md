# Tag-Based Requirement Set Filtering Implementation

## Overview

This document describes the implementation of role-based tag filtering for requirement sets in the lookup system. The feature allows filtering requirement sets by user roles like "Gastroenterologist", "Student", "Professor", etc.

## Tag Management

### Adding New Tags via YAML

Tags can be easily added by editing the YAML configuration file:

**File**: `endoreg_db/data/tag/requirement_set_tags.yaml`

**Format:**
```yaml
- model: endoreg_db.tag
  fields:
    name: "Your Tag Name"
```

**Example - Adding a new "Nurse" tag:**
```yaml
# User role tags for requirement set filtering
- model: endoreg_db.tag
  fields:
    name: "Gastroenterologist"

- model: endoreg_db.tag
  fields:
    name: "Nurse"  # <-- New tag added here
```

**Loading Tags into Database:**
```bash
uv run python manage.py load_tag_data --verbose
```

This command reads all YAML files in `endoreg_db/data/tag/` and creates/updates tags in the database.

### Default Tags

The following role-based tags are pre-configured:
- **Gastroenterologist** - For specialist-level requirement sets
- **Student** - For basic/educational requirement sets  
- **Professor** - For academic/teaching requirement sets
- **Terminology Expert** - For terminology-focused requirement sets
- **Default Prototype** - For default/prototype requirement sets

### Assigning Tags to Requirement Sets

Tags can be assigned to requirement sets via:

1. **Django Admin Interface**:
   - Navigate to RequirementSet in admin
   - Select tags from the many-to-many field

2. **Django Shell**:
   ```python
   from endoreg_db.models import RequirementSet, Tag
   
   req_set = RequirementSet.objects.get(name="Advanced Colonoscopy QA")
   gastro_tag = Tag.objects.get(name="Gastroenterologist")
   req_set.tags.add(gastro_tag)
   ```

3. **Programmatically in Code**:
   ```python
   req_set.tags.set([tag1, tag2])  # Replace all tags
   req_set.tags.add(tag)            # Add one tag
   req_set.tags.remove(tag)         # Remove one tag
   ```

## Architecture

### 1. Data Model

**Tag Model** (`endoreg_db/models/other/tag.py`)
- Simple tag model with unique names
- Many-to-many relationship with RequirementSet

**RequirementSet Model** (already had tags field)
```python
tags = models.ManyToManyField("Tag", blank=True, related_name="requirement_sets")
```

### 2. Service Layer

**File**: `endoreg_db/services/lookup_service.py`

#### Modified Functions:

**`requirement_sets_for_patient_exam(pe, user_tags=None)`**
- Filters requirement sets by tag names if provided
- Uses Django ORM `.filter(tags__name__in=user_tags).distinct()`
- Returns all sets if no tags specified (backward compatible)

**`build_initial_lookup(pe, user_tags=None)`**
- Passes user_tags to requirement_sets_for_patient_exam
- Builds initial lookup data with filtered sets

**`create_lookup_token_for_pe(pe_id, user_tags=None)`**
- Accepts optional user_tags parameter
- Passes tags through to build_initial_lookup

### 3. API Layer

**File**: `endoreg_db/views/requirement/lookup.py`

#### Modified Endpoint:

**`LookupViewSet.init()`**
- Accepts `user_tags` in request body (list of strings)
- Validates tag format
- Passes tags to `create_lookup_token_for_pe`

**Request Format:**
```json
{
  "patient_examination_id": 123,
  "user_tags": ["Gastroenterologist", "Professor"]
}
```

### 4. Serializers

**File**: `endoreg_db/serializers/requirements/requirement_sets.py`

**RequirementSetSerializer**
- Includes optional `tags` field (read-only)
- Uses `SlugRelatedField` to return tag names (not IDs)
- Smart `to_representation()` that excludes empty tags

**requirement_set_to_dict() function**
- Refactored to avoid N+1 queries
- Includes tags in output if they exist
- Proper prefetching of related data

### 5. Management Command

**File**: `endoreg_db/management/commands/load_requirement_set_tags.py`

**Command**: `uv run python manage.py load_requirement_set_tags`

Creates default tags:
- Gastroenterologist
- Student
- Professor
- Terminology Expert
- Default Prototype

## Usage

### 1. Seed Default Tags

```bash
uv run python manage.py load_requirement_set_tags
```

### 2. Assign Tags to Requirement Sets

Via Django admin or programmatically:
```python
from endoreg_db.models import RequirementSet, Tag

req_set = RequirementSet.objects.get(name="Advanced Colonoscopy QA")
gastro_tag = Tag.objects.get(name="Gastroenterologist")
req_set.tags.add(gastro_tag)
```

### 3. Frontend Integration

Initialize lookup with tag filtering:
```javascript
const response = await fetch('/api/requirement/lookup/init/', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    patient_examination_id: 123,
    user_tags: ['Gastroenterologist', 'Professor']  // Optional
  })
});
```

### 4. Use Serializer

```python
from endoreg_db.serializers.requirements.requirement_sets import RequirementSetSerializer
from endoreg_db.models import RequirementSet

# With prefetching for optimal performance
req_sets = RequirementSet.objects.prefetch_related('tags').all()
serializer = RequirementSetSerializer(req_sets, many=True)
# Output includes tags field
```

## Testing

**Test File**: `tests/services/test_lookup_tag_filtering.py`

Run tests:
```bash
uv run pytest tests/services/test_lookup_tag_filtering.py -v
```

Test coverage:
- ✅ No tag filter returns all requirement sets
- ✅ Single tag filters correctly
- ✅ Multiple tags use OR logic
- ✅ Non-existent tags return empty
- ✅ Token creation respects tags
- ✅ Initial lookup respects tags
- ✅ Empty tag list returns empty

## Backward Compatibility

- ✅ `user_tags=None` returns all requirement sets (default behavior)
- ✅ Existing API calls without `user_tags` parameter work unchanged
- ✅ Serializer excludes tags field if empty (no breaking changes)
- ✅ All existing tests pass

## Performance Considerations

1. **Database Queries**:
   - Tag filtering uses `.distinct()` to avoid duplicates
   - Serializer uses `prefetch_related('tags')` to avoid N+1 queries

2. **Caching**:
   - Lookup tokens cache filtered requirement sets
   - No re-filtering needed during session

3. **Optimization Tips**:
   - Prefetch tags when serializing multiple requirement sets
   - Consider indexing Tag.name if large tag datasets

## Future Enhancements

1. **Tag Hierarchies**: Support parent/child tag relationships
2. **Dynamic Tags**: User-defined tags beyond defaults
3. **Tag Combinations**: Support AND logic in addition to OR
4. **Tag Metadata**: Add descriptions, colors, icons to tags
5. **Permission-Based Tags**: Restrict tag visibility by user role

## Migration Notes

No database migrations required - the Tag model and tags field on RequirementSet already existed. Only code changes were necessary.

## References

- Tag Model: `endoreg_db/models/other/tag.py`
- RequirementSet Model: `endoreg_db/models/requirement/requirement_set.py`
- Lookup Service: `endoreg_db/services/lookup_service.py`
- Lookup ViewSet: `endoreg_db/views/requirement/lookup.py`
- Serializers: `endoreg_db/serializers/requirements/requirement_sets.py`
- Tests: `tests/services/test_lookup_tag_filtering.py`
