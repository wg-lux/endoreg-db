# Major Changes
# Major Changes

## Improve CLI

added files:

- dev/dev_settings.py
- manage.py

We can now run migrations and test commands directly from within the endoreg-db repository

## Add Db Models

- Risk, RiskType
- Requirement, RequirementSet
- RequirementValueType, RequirementOperator

_Explanation:_
Risk is a Model to define risks.
Risks are grouped by risk_type (e.g. thrombosis, bleeding, perforation, etc). Risks within a group (e.g., "bleeding_low", "bleeding_high") are ordered by risk value (e.g., 1, 3; float, increasing meaning a higher risk). An unknown risk is indicated by 0. In future, protective factors might be reflected by negative risks

Risk and RequirementSet have a Many2Many relationship which is defined in the risk model. In future, we will also implement something like an "Action" which will have a Many2Many relationship to RequirementSet (e.g., follow-up within 6 Months)

RequirementSet and Requirement have a Many2Many relationship defined in the RequirementSet.

RequirementSet has an "evaluate" function which expects fetches all linked Requirements and call

Requirement references RequirementValueType (e.g. LabValue, PerformedIntervention, PlannedIntervention) as ForeignKey. This also determines the required inputs for the evaluate function.

Requirement has an evaluate function taking kwargs. Depending on the RequirementValueType, specific kwargs must be available.

Requirement references RequirementOperator as ForeignKey.

A Requirement has may have a text, json, or numeric value (each stored in different fields)

The RequirementOperator defines functions to evaluate a requirement by fetching the correct fields from the required objects (for lab values we need to fetch the associated patients lab values including the samples reference lab value and so on)

# To-Do

## Updates to contribution Guide

- Django classes should have correct typing implementations for Foreign keys and Many2Many Relationships on both sides (for example take a look at models for: Risk / RiskType)

- init files should declare an `__all__` variable

- If development ends up causing many new migration files (since we tried around a bit and ran "makemigrations" a couple of times), we should delete the new migration files and create a single final migration step by running "makemigrations" again.

- add unit type model to filter units by type (e.g. time related units etc.)

- add action model (similar to requirement)

- add actionType model (e.g., recommended, set_value, ...)
