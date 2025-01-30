from .model_meta import ModelMeta

# get latest model meta by model name

# TODO MOVE THIS TO A QUERY FILE
def get_latest_model_meta_by_model_name(model_name):
    model_meta = ModelMeta.objects.filter(name=model_name).order_by('-version').first()
    return model_meta
