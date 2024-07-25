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

    _files = [f for f in os.listdir(dir_path) if f.endswith('.yaml')]
    # sort
    _files.sort()
    for file in _files:
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
        print(entry)

        # Handle foreign keys and many-to-many relationships
        for fk_field, fk_model in zip(foreign_keys, foreign_key_models):
            print(fk_field, fk_model)
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
                    if model == "endoreg_db.case_template_rule":
                        print(fk_model, target_keys)
                    obj = fk_model.objects.get_by_natural_key(target_keys)
                except ObjectDoesNotExist:
                    if verbose:
                        command.stdout.write(command.style.WARNING(f"{fk_model.__name__} with key {target_keys} not found"))
                    continue
                fields[fk_field] = obj
    
        # Create or update the main object
        if name is None:
            obj, created = model.objects.get_or_create(**fields)
        else:
            obj, created = model.objects.update_or_create(defaults=fields, name=name)
        if created and verbose:
            command.stdout.write(command.style.SUCCESS(f'Created {model.__name__} {name}'))
        elif verbose:
            command.stdout.write(command.style.WARNING(f'Skipped {model.__name__} {name}, already exists'))

        # Set many-to-many relationships
        for field_name, related_objs in m2m_relationships.items():
            getattr(obj, field_name).set(related_objs)
