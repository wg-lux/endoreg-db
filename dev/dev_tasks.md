# ToDo
- add target_types: Many2Many to RequirementTypes to RequirementSet
- add generate function to RequirementSet; Looks up target models in "target_types"; basically "serializes" rules to yield the defined target models as new objects
- add validate function which validates the full requirement set; a requirement set is evaluated by calling .evaluate() of each linked requirement and requirement_set; Make sure to collect unique names of requirements and links and use them to detect and handle circular chains of requirement sets correctly, initially we should raise an error
- add test which tries to generate all required objects using the new logic
