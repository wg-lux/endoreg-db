from ...models.requirement.requirement_set import RequirementSet
from .requirement_schema import RequirementSetSchema

def requirement_set_to_dict(requirement_set: RequirementSet) -> dict:
    """
    Convert a RequirementSet instance to a dictionary representation.
    
    Args:
        requirement_set (RequirementSet): The RequirementSet instance to convert.
    
    Returns:
        dict: A dictionary representation of the RequirementSet.
    """
    
    requirement_set = RequirementSet.objects.select_related(
        "created_by", "updated_by"
    ).prefetch_related(
        "requirements", "links"
    ).get(id=requirement_set.id)

    links = requirement_set.links_to_sets()
    
    return {
        "id": requirement_set.id,
        "name": requirement_set.name,
        "description": requirement_set.description,
        "requirements": [req.to_dict() for req in requirement_set.requirements.all()],
        "links": [link.model_dump() for link in links]
    }