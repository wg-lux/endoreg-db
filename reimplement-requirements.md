# Requirement logic
Our goal is to reimplement the logic behind the implemented "requirements" in a more efficient and maintainable way.

## Definitions


### RequirementOperator

### Requirement
A Requirement is a Django model that represents a specific, evaluatable condition. The model has the following fields:
- `name`: A string representing the name of the requirement. Acts as a unique identifier.
- `description`: A text field providing a detailed description of the requirement.
- `requirement_operator`: Foreign Key to `RequirementOperator`, indicating the operator used for evaluation.
- `input`: A JSON field containing the definition of the expected input needed for evaluation. Is internally parsed into a `RequirementInputParser` object.
- `output`: A JSON field containing the definition output after evaluation. Is internally parsed into a `RequirementOutputParser` object.

### RequirementSet

### RequirementInputParser #TODO
Pydantic model that represents the inputs required for evaluating a requirement. It includes:

### RequirementOutputParser #TODO
Pydantic model that represents the outputs generated after evaluating a requirement. It includes:

### ReqSetEvaluationContext #TODO
Pydantic model that encapsulates the context in which a requirement is evaluated. Needed during the evaluation process to provide necessary data and state.

### ReqEvaluationContext #TODO