import os
import yaml

def load_model_data_from_yaml(
        command,
        model_name,
        metadata,
        verbose,
    ):

    if verbose:
        command.stdout.write(f"Start Loading {model_name}")
    model = metadata["model"]
    dir = metadata["dir"]
    foreign_keys = metadata["foreign_keys"]
    foreign_key_models = metadata["foreign_key_models"]

    for file in [f for f in os.listdir(dir) if f.endswith('.yaml')]:
        with open(os.path.join(dir, file), 'r') as f:
            yaml_data = yaml.safe_load(f)
        
        load_data_with_foreign_keys(
            command,
            model,
            yaml_data,
            foreign_keys,
            foreign_key_models,
            verbose
        )

def load_data_with_foreign_keys(command, model, yaml_data, foreign_keys, foreign_key_models, verbose):
            # Since pathology types is a ManyToMany field, we need to hack arount
            for entry in yaml_data:
                fields = entry.get('fields', {})
                name = fields.pop('name', None)
                many_to_many_tuples = []
                foreign_key_tuples = zip(foreign_keys, foreign_key_models)
                for foreign_key, foreign_key_model in foreign_key_tuples:
                    target_natural_key = fields.pop(foreign_key, None)
   

                    if isinstance(target_natural_key, list):
                        # the field is a Many to X field.
                        fk_objects = [foreign_key_model.objects.get_by_natural_key(_) for _ in target_natural_key]
                        fk_tuple = (foreign_key, fk_objects)
                        many_to_many_tuples.append(fk_tuple)
                        continue
                    # Use the natural key to look up the related object
                    try:
                        obj = foreign_key_model.objects.get_by_natural_key(target_natural_key)
                    except model.DoesNotExist:
                        command.stderr.write(command.style.ERROR(f'{model.__name__} with natural key {target_natural_key} does not exist. Skipping {name}.'))
                        raise Exception(f'{model.__name__} with natural key {target_natural_key} does not exist. Skipping {name}.')

                    # Assign the related object to the field
                    fields[foreign_key] = obj

                if name:
                    obj, created = model.objects.get_or_create(name=name, defaults=fields)
                else:
                    obj, created = model.objects.get_or_create(**fields)
                if many_to_many_tuples:
                    for fk, fk_objects in many_to_many_tuples:
                        getattr(obj, fk).set(fk_objects)

                if created and verbose:
                    command.stdout.write(command.style.SUCCESS(f'Created {model.__name__} {name}'))
                elif verbose:
                    command.stdout.write(command.style.WARNING(f'Skipped {model.__name__} {name}, already exists'))
