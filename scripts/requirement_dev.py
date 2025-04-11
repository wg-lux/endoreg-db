from endoreg_db.models import Requirement, RequirementSet
from pathlib import Path
import yaml
from pprint import pprint

out_dir = Path("./data/requirement_dev/")
out_dir.mkdir(parents=True, exist_ok=True)

requirement_set_bleeding_high = RequirementSet.objects.get(
    name="endoscopy_bleeding_risk_high"
)

requirements = requirement_set_bleeding_high.requirements.all()

for requirement in requirements:
    print(f"Requirement: {requirement}")
    print(f"Requirement Set: {requirement.requirement_sets.all()}")
    print(f"Requirement Type: {requirement.requirement_types.all()}")
    print(f"Operators: {requirement.operators.all()}")
    print(f"Unit: {requirement.unit}")
    print(f"Examinations: {requirement.examinations.all()}")
    print(f"Examination Indications: {requirement.examination_indications.all()}")
    print(f"Diseases: {requirement.diseases.all()}")

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
