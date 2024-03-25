import os
import yaml
from django.core.exceptions import ObjectDoesNotExist

def load_model_data_from_yaml(command, model_name, metadata, verbose):
    """
    Load model data from YAML files.
    
    Args:
        command: Command object for stdout writing.
        model_name: Name of the model being loaded.
        metadata: Metadata including directory and foreign key information.
        verbose: Boolean indicating whether to print verbose output.
    """
    if verbose:
        command.stdout.write(f"Start loading {model_name}")
    model = metadata["model"]
    dir_path = metadata["dir"]
    foreign_keys = metadata["foreign_keys"]
    foreign_key_models = metadata["foreign_key_models"]

    for file in [f for f in os.listdir(dir_path) if f.endswith('.yaml')]:
        with open(os.path.join(dir_path, file), 'r') as file:
            yaml_data = yaml.safe_load(file)
        
        load_data_with_foreign_keys(command, model, yaml_data, foreign_keys, foreign_key_models, verbose)

def load_data_with_foreign_keys(command, model, yaml_data, foreign_keys, foreign_key_models, verbose):
    """
    Load data handling foreign keys and many-to-many relationships.
    
    Args:
        command: Command object for stdout writing.
        model: The Django model for the data.
        yaml_data: Data loaded from YAML.
        foreign_keys: List of foreign keys.
        foreign_key_models: Corresponding models for the foreign keys.
        verbose: Boolean indicating whether to print verbose output.
    """
    for entry in yaml_data:
        fields = entry.get('fields', {})
        name = fields.pop('name', None)
        m2m_relationships = {}  # Store many-to-many relationships

        # Handle foreign keys and many-to-many relationships
        for fk_field, fk_model in zip(foreign_keys, foreign_key_models):
            target_keys = fields.pop(fk_field, None)
            
            # Ensure the foreign key exists
            if target_keys is None:
                if verbose:
                    command.stdout.write(command.style.WARNING(f"Foreign key {fk_field} not found in fields"))
                continue  # Skip if no foreign key provided

            # Process many-to-many fields or foreign keys
            if isinstance(target_keys, list):  # Assume many-to-many relationship
                related_objects = []
                for key in target_keys:
                    obj, created = fk_model.objects.get_or_create(name=key)
                    if created and verbose:
                        command.stdout.write(command.style.SUCCESS(f"Created {fk_model.__name__} {key}"))
                    related_objects.append(obj)
                m2m_relationships[fk_field] = related_objects
            else:  # Single foreign key relationship
                try:
                    obj = fk_model.objects.get_by_natural_key(target_keys)
                except ObjectDoesNotExist:
                    if verbose:
                        command.stdout.write(command.style.WARNING(f"{fk_model.__name__} with key {target_keys} not found"))
                    continue
                fields[fk_field] = obj

        # Create or update the main object
        obj, created = model.objects.update_or_create(defaults=fields, name=name)
        if created and verbose:
            command.stdout.write(command.style.SUCCESS(f'Created {model.__name__} {name}'))
        elif verbose:
            command.stdout.write(command.style.WARNING(f'Skipped {model.__name__} {name}, already exists'))

        # Set many-to-many relationships
        for field_name, related_objs in m2m_relationships.items():
            getattr(obj, field_name).set(related_objs)


# def load_model_data_from_yaml(
#         command,
#         model_name,
#         metadata,
#         verbose,
#     ):

#     if verbose:
#         command.stdout.write(f"Start Loading {model_name}")
#     model = metadata["model"]
#     dir = metadata["dir"]
#     foreign_keys = metadata["foreign_keys"]
#     foreign_key_models = metadata["foreign_key_models"]

#     for file in [f for f in os.listdir(dir) if f.endswith('.yaml')]:
#         with open(os.path.join(dir, file), 'r') as f:
#             yaml_data = yaml.safe_load(f)
        
#         load_data_with_foreign_keys(
#             command,
#             model,
#             yaml_data,
#             foreign_keys,
#             foreign_key_models,
#             verbose
#         )

# def load_data_with_foreign_keys(command, model, yaml_data, foreign_keys, foreign_key_models, verbose):
#             # Since pathology types is a ManyToMany field, we need to hack arount
#             for entry in yaml_data:
#                 fields = entry.get('fields', {})
#                 name = fields.pop('name', None)
#                 many_to_many_tuples = []
#                 foreign_key_tuples = zip(foreign_keys, foreign_key_models)
#                 for foreign_key, foreign_key_model in foreign_key_tuples:
#                     # target_natural_key = fields.pop(foreign_key, None)
#                     # get the target natural key, if it exists, should not alter fields
#                     target_natural_key = fields.get(foreign_key, None)
#                     assert target_natural_key, f"Foreign Key {foreign_key} not found in fields {fields}"
   
#                     if (foreign_key == "first_names") or (foreign_key == "last_names"):
#                         if isinstance(target_natural_key, list):
#                             # For first_names and last_names, the field is a Many to Many field
#                             # if names dont exist yet, we create them
#                             fk_objects = []
#                             for __name in target_natural_key:
#                                 obj, created = foreign_key_model.objects.get_or_create(name=__name)
#                                 if created:
#                                     command.stdout.write(command.style.SUCCESS(f'Created {foreign_key_model.__name__} {__name}'))
#                                 # fk_objects.append(obj)

#                             fk_tuple = (foreign_key, fk_objects)
#                             many_to_many_tuples.append(fk_tuple)
#                         continue

#                     if isinstance(target_natural_key, list):
#                         # the field is a Many to X field.
#                         fk_objects = [foreign_key_model.objects.get_by_natural_key(_) for _ in target_natural_key]
#                         fk_tuple = (foreign_key, fk_objects)
#                         many_to_many_tuples.append(fk_tuple)
#                         continue
#                     # Use the natural key to look up the related object
#                     try:
#                         obj = foreign_key_model.objects.get_by_natural_key(target_natural_key)
#                     except:
#                         # commandline log that the object was not found
#                         command.stdout.write(command.style.WARNING(f'Object {foreign_key_model.__name__} {target_natural_key} not found'))
#                         # commandline log entry
#                         command.stdout.write(command.style.WARNING(_log))
#                         # try to create by name if name is available
#                         # create defaults dict from fields using the models fields
#                         _field_names = [_.name for _ in foreign_key_model._meta.fields]
#                         _defaults = {k: v for k, v in fields.items() if (k in _field_names) and v}
                        
#                         if target_natural_key:
#                         # commandlie log
#                             command.stdout.write(command.style.SUCCESS(f'Creating {foreign_key_model.__name__} {name} with defaults {_defaults}'))
#                             obj, created = model.objects.get_or_create(
#                                 name = target_natural_key,
#                                 defaults=_defaults
#                             )
#                     # Assign the related object to the field
#                     fields[foreign_key] = obj

#                 if name:
#                     try:
#                         obj, created = model.objects.get_or_create(name=name, defaults=fields)
#                     except:
#                         # commandlinelog to print name, fields, target foreign key
#                         command.stdout.write(command.style.WARNING(f'Object {model.__name__} {name} not found'))
#                 else:
#                     obj, created = model.objects.get_or_create(**fields)
#                 if many_to_many_tuples:

#                     for fk, fk_objects in many_to_many_tuples:
#                         getattr(obj, fk).set(fk_objects)

#                 if created and verbose:
#                     command.stdout.write(command.style.SUCCESS(f'Created {model.__name__} {name}'))
#                 elif verbose:
#                     command.stdout.write(command.style.WARNING(f'Skipped {model.__name__} {name}, already exists'))
