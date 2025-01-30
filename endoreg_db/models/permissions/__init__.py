from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import Permission
from endoreg_db.models import *  # Import your models here
from django.db import transaction

# Step 1: Define model to category mappings
# Assuming every model class name is unique across your entire Django project
model_categories = {
    'SensitiveModel1': 'sensitive',
    'SensitiveModel2': 'sensitive',
    'DevelopmentModel1': 'development',
    'DevelopmentModel2': 'development',
    # Add all models you have, mapping them to either 'sensitive', 'development', or 'all'
}

# Step 2: Define permissions for each category
category_permissions = {
    'sensitive': ['view', 'edit', 'delete'],
    'development': ['view', 'edit'],
    'all': ['view'],
}

@transaction.atomic
def create_permissions_for_all_models():
    for model_class_name, category in model_categories.items():
        model_class = globals().get(model_class_name)
        if model_class is None:
            print(f"Model {model_class_name} not found.")
            continue
        
        content_type = ContentType.objects.get_for_model(model_class)
        permissions = category_permissions.get(category, [])

        for permission_codename in permissions:
            permission_name = f"Can {permission_codename} {model_class_name}"
            permission, created = Permission.objects.get_or_create(
                codename=f"{permission_codename}_{model_class_name.lower()}",
                defaults={'name': permission_name, 'content_type': content_type},
            )
            if created:
                print(f"Created permission: {permission_name}")

# Run the function to create and assign permissions based on categories
create_permissions_for_all_models()
