from django.db import models

class CaseTemplateManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplate(models.Model):
    """
    A class representing a case template.

    Attributes:
        name (str): The name of the case template. This is the natural key.
        case_template_type (CaseTemplateType): The type of the case template.
        description (str): A description of the case template.

    """
    name = models.CharField(max_length=255, unique=True)
    template_type = models.ForeignKey("CaseTemplateType", on_delete=models.CASCADE, related_name="case_templates")
    description = models.TextField(blank=True, null=True)

    rules = models.ManyToManyField(
        "CaseTemplateRule",
    )

    secondary_rules = models.ManyToManyField(
        "CaseTemplateRule",
        related_name="secondary_rules"
    )

    objects = CaseTemplateManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_rules(self):
        rules = self.rules.all()
        return rules
    
    def get_secondary_rules(self):
        rules = self.secondary_rules.all()
        return rules
    
    def _assert_max_one_create_patient_rule(self):
        """Asserts that there is at most one rule with the rule_type__name "create-object" and target_model__name "Patient"."""
        create_patient_rules = self.rules.filter(rule_type__name="create-object", target_model__name="Patient")
        if len(create_patient_rules) > 1:
            raise ValueError("There can be at most one rule with the rule_type__name 'create-object' and target_model__name 'Patient'.")

    # custom save method which runs the _assert_max_one_create_patient_rule method and others
    def save(self, *args, **kwargs):
        # self._assert_max_one_create_patient_rule() #TODO Fails on first save since many to many can only be used if object has an id
        super().save(*args, **kwargs)

    def get_create_patient_rule(self):
        """Filter the rules for the rule which has the rule_type__name "create-object" and target_model__name "Patient".
        Also makes sure, that only 1 is returned. If there is no such rule, get default create patient rule.
        """
        from endoreg_db.models.case_template.case_template_rule import CaseTemplateRule
        create_patient_rules = self.rules.filter(rule_type__name="create-object", target_model="Patient")
        if len(create_patient_rules) > 1:
            raise ValueError("There can be at most one rule with the rule_type__name 'create-object' and target_model__name 'Patient'.")
        elif len(create_patient_rules) == 0:
            create_patient_rules = CaseTemplateRule.objects.get_default_create_patient_rule()
        return create_patient_rules[0]
    
    def get_create_patient_medication_schedule_rule(self):
        """Filter the rules for the rule which has the rule_type__name "create-object" and target_model__name "PatientMedicationSchedule".
        Also makes sure, that only 1 is returned. If there is no such rule, get default create patient medication schedule rule.
        """
        from endoreg_db.models.case_template.case_template_rule import CaseTemplateRule
        create_patient_medication_schedule_rules = self.rules.filter(rule_type__name="create-object", target_model="PatientMedicationSchedule")
        if len(create_patient_medication_schedule_rules) > 1:
            raise ValueError("There can be at most one rule with the rule_type__name 'create-object' and target_model__name 'PatientMedicationSchedule'.")
        elif len(create_patient_medication_schedule_rules) == 0:
            from warnings import warn
            warn("No create patient medication schedule rule found. Using default create patient medication schedule rule.")
            create_patient_medication_schedule_rules = CaseTemplateRule.objects.get_default_create_patient_medication_schedule_rule()
        return create_patient_medication_schedule_rules[0]
