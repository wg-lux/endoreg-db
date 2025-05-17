from tqdm import tqdm
import os
import json
import shutil
import random
from collections import defaultdict

# Define the directory paths
base_dir = "/run/media/setup-user/8f1cd448-7aef-4eb8-9acb-d05869ea1923"
jsons_dir = os.path.join(base_dir, "jsons")
output_json_dir = "./jsons"
output_image_dir = "./images"

# Remove existing output directories
shutil.rmtree(output_json_dir, ignore_errors=True)
shutil.rmtree(output_image_dir, ignore_errors=True)

# Ensure the output directories exist
os.makedirs(output_json_dir, exist_ok=True)
os.makedirs(output_image_dir, exist_ok=True)

# Initialize dictionaries to track examples and counts
key_stats = defaultdict(lambda: defaultdict(int))
examples = defaultdict(list)

def normalize_value(value):
    """
    Converts complex data types into a hashable string representation.
    """
    if isinstance(value, (list, dict)):
        return str(value)
    return value

def explore_json_file(json_file):
    """
    Reads a JSON file and updates key_stats and examples for specific keys.
    Also copies the corresponding image if the "path" key exists.
    """
    try:
        with open(json_file, 'r', encoding='utf-8') as file:
            data = json.loads(file.read())
    except json.JSONDecodeError as e:
        print(f"Error reading {json_file}: {e}")
        return False  # Skip processing this file
    
    # Update key_stats and gather examples
    extracted_keys = False
    for key, value in data.items():
        if key in ["predictions_smooth", "labels_new"]:
            for sub_key, sub_value in value.items():
                sub_value_normalized = normalize_value(sub_value)
                key_stats[f"{key}.{sub_key}"][sub_value_normalized] += 1

                # Add example if fewer than 50 have been collected
                if len(examples[f"{key}.{sub_key}"]) < 50:
                    examples[f"{key}.{sub_key}"].append(data)
                    extracted_keys = True
        else:
            value_normalized = normalize_value(value)
            key_stats[key][value_normalized] += 1
    
    # Copy the corresponding image if "path" key exists
    if "path" in data:
        image_path = os.path.join(base_dir, data["path"])
        if os.path.exists(image_path):
            try:
                shutil.copy(image_path, output_image_dir)
            except Exception as e:
                print(f"Error copying image {image_path}: {e}")
    
    return extracted_keys

def explore_directory(jsons_dir):
    """
    Iterates through the JSON files in a randomized order and processes them.
    """
    # Gather all JSON files
    json_files = []
    for root, _, files in os.walk(jsons_dir):
        for file in tqdm(files):
            if file.endswith(".json"):
                json_files.append(os.path.join(root, file))
    
    # Randomize the order of JSON files
    random.shuffle(json_files)

    # Process each JSON file
    for json_file in tqdm(json_files):
        extracted = explore_json_file(json_file)

        # Copy file if it contained relevant keys
        if extracted:
            shutil.copy(json_file, output_json_dir)

# Start the exploration process
explore_directory(jsons_dir)

# Save key_stats and examples to separate summary files
with open(os.path.join(output_json_dir, "key_stats.json"), "w", encoding="utf-8") as stats_file:
    json.dump({k: dict(v) for k, v in key_stats.items()}, stats_file, indent=4)

with open(os.path.join(output_json_dir, "examples.json"), "w", encoding="utf-8") as examples_file:
    json.dump({k: [e["_id"] for e in v] for k, v in examples.items()}, examples_file, indent=4)

# Display summary
print("Exploration complete!")
print(f"Total keys analyzed: {len(key_stats)}")
print(f"Total examples collected: {sum(len(v) for v in examples.values())}")
