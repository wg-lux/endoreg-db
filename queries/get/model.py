from endoreg_db.models import (
    ModelMeta,
)

def get_latest_model_by_name(model_name):
    """
    Expects model name. Fetches models by name from database, sorts by version and returns latest version.
    """
    models = ModelMeta.objects.filter(name=model_name).order_by('-version')
    if len(models) == 0:
        return None
    else:
        return models[0]
