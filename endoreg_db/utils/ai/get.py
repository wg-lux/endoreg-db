from endoreg_db.models import ModelMeta

def get_latest_model_meta_by_model_name(model_name):
    model_meta = ModelMeta.objects.filter(name=model_name).order_by('-version').first()
    return model_meta
