from endoreg_db.models import Requirement
from pathlib import Path
import yaml
from pprint import pprint

out_dir = Path("./data/requirement_dev/")
out_dir.mkdir(parents=True, exist_ok=True)

requirement_set_bleeding_high = Requirement.objects.get(name="Bleeding High")

# requirement = requirements[0]

# pprint(requirement.__dict__)
# pprint(requirement.requirement_types.all())
# pprint(requirement.operators.all())
# pprint(requirement.unit)
# pprint(requirement.examinations.all())
# pprint(requirement.examination_indications.all())
# pprint(requirement.diseases.all())

# yaml_file = out_dir / "requirement.yaml"
# with open(yaml_file, "w") as f:
#     yaml.dump(requirement.__dict__, f)
