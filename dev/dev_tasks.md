# Tasks
## Requirement Validation
Context:
- Data Models: endoreg_db/data/requirement_set, endoreg_db/data/requirement_set_type, endoreg_db/data/requirement_operator, endoreg_db/data/requirement, endoreg_db/data/requirement_type
- Django Models: endoreg_db/models/requirement
- Utility Models / Functions: endoreg_db/utils/links
- Data Model Dict: endoreg_db/models/requirement/requirement_evaluation/requirement_type_parser.py

1. Analyse current code of the requirement, requirementType, RequirementSet, Requirement Operator to understand the current state of development
2. Make a plan to implement a validate function:

*validate function functionality:*
- expects inputs corresponding to the RequirementType (we can look up RequirementType.name in data_model_dict to get the expected input models)
- fetches the input objects "links" property which will yield a RequirementLinks object
- Evaluate the Requirements RequirementLinks, the requirement operator and the inputs to return either true or false
    - use the linked requirement operator to identify the correct evaluation logic (e.g., models_match_any)

3. Implement the Validate Function and use it to modify our current test (tests/requirement/test_requirement_endo_bleeding.py, tests/requirement/links/test_requirement_links.py)
