from endoreg_db.models import Center
from typing import Optional

def get_centers() -> Center:
    """
    Returns all Center objects from the database.
    """
    return Center.objects.all()

def get_center_by_name(name) -> Optional[Center]:
    """Retrieve a Center object by its name.

    Args:
        name (str): The name of the center to retrieve.

    Returns:
        Optional[Center]: The Center object with the given name, or None if it does not exist.
    """
    return Center.objects.get(name=name)

def get_center_by_id(id) -> Optional[Center]:
    """Retrieve a Center object by its id.

    Args:
        id (int): The id of the center to retrieve.

    Returns:
        Optional[Center]: The Center object with the given id, or None if it does not exist.
    """
    return Center.objects.get(id=id)

def get_center_by_natural_key(name: str) -> Optional[Center]:
    """
    Retrieve a Center object by its natural key.

    Args:
        name: The name of the center to retrieve.

    Returns:
        The Center object with the given name, or None if it does not exist.
    """
    return Center.objects.get_by_natural_key(name=name)
