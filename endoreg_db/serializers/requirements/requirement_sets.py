from rest_framework import serializers

from endoreg_db.models.requirement.requirement_set import RequirementSet


class RequirementSetSerializer(serializers.ModelSerializer):
    """
    Serializer for RequirementSet model with optional tag field.

    This serializer provides a flexible representation of requirement sets,
    including role-based tags for filtering (e.g., "Gastroenterologist", "Student").

    Fields:
        id: Primary key
        name: Display name of the requirement set
        description: Optional description text
        requirement_set_type: Type classification
        tags: List of tag names (optional, read-only)
    """

    tags = serializers.SlugRelatedField(
        many=True, read_only=True, slug_field="name", required=False
    )

    requirement_set_type = serializers.CharField(
        source="requirement_set_type.name", read_only=True, allow_null=True
    )

    class Meta:
        model = RequirementSet
        fields = ["id", "name", "description", "requirement_set_type", "tags"]

    def to_representation(self, instance):
        """
        Customize representation to exclude tags field if empty or not prefetched.

        This prevents N+1 query issues and keeps the response clean when tags
        aren't needed or weren't prefetched in the queryset.
        """
        representation = super().to_representation(instance)

        # Only include tags if they exist and were prefetched
        if not representation.get("tags"):
            representation.pop("tags", None)

        return representation


def requirement_set_to_dict(requirement_set: RequirementSet) -> dict:
    """
    Convert a RequirementSet instance to a dictionary representation.

    This function performs additional queries to fetch related data including
    requirements and linked sets. Use RequirementSetSerializer for simpler
    representations without the overhead of additional queries.

    Args:
        requirement_set: The RequirementSet instance to convert.

    Returns:
        Dictionary representation of the RequirementSet with full nested details:
        - id, name, description: Basic metadata
        - requirements: List of requirement dictionaries
        - links: List of linked requirement set data
        - tags: List of tag names (if any exist)
    """
    # Fetch the requirement set with all related data to avoid N+1 queries
    requirement_set_full = (
        RequirementSet.objects.select_related("requirement_set_type")
        .prefetch_related("requirements", "links_to_sets", "tags")
        .get(pk=requirement_set.pk)
    )

    # Get linked requirement sets
    linked_sets = requirement_set_full.links_to_sets.all()

    result = {
        "id": requirement_set_full.pk,
        "name": requirement_set_full.name,
        "description": requirement_set_full.description or "",
        "requirement_set_type": requirement_set_full.requirement_set_type.name
        if requirement_set_full.requirement_set_type
        else None,
        "requirements": [
            {"id": req.pk, "name": req.name, "description": req.description or ""}
            for req in requirement_set_full.requirements.all()
        ],
        "linked_sets": [
            {"id": link.pk, "name": link.name, "description": link.description or ""}
            for link in linked_sets
        ],
    }

    # Add tags if they exist
    tags = list(requirement_set_full.tags.values_list("name", flat=True))
    if tags:
        result["tags"] = tags

    return result
