def build_multilingual_response(obj, include_choices=False, classification_id=None):
    """
    Helper to build a multilingual response dict for an object.
    If include_choices is True, adds a 'choices' key with multilingual dicts for each choice.
    If classification_id is given, adds 'classificationId' to each choice.
    """
    data = {
        'id': obj.id,
        'name': getattr(obj, 'name', ''),
        'name_de': getattr(obj, 'name_de', ''),
        'name_en': getattr(obj, 'name_en', ''),
        'description': getattr(obj, 'description', ''),
        'description_de': getattr(obj, 'description_de', ''),
        'description_en': getattr(obj, 'description_en', ''),
    }
    if hasattr(obj, 'required'):
        data['required'] = getattr(obj, 'required', True)
    if include_choices:
        data['choices'] = [
            build_multilingual_response(choice, include_choices=False, classification_id=classification_id or obj.id)
            for choice in obj.get_choices()
        ]
        for choice_dict in data['choices']:
            choice_dict['classificationId'] = classification_id or obj.id
    if classification_id is not None and not include_choices:
        data['classificationId'] = classification_id
    return data