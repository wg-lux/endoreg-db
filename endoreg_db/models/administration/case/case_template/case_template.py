from django.db import models

class CaseTemplateManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplate(models.Model):
    """
    Represents a case template.

    Attributes:
        name (str): The unique name of the case template.
        template_type (ForeignKey): The type of the case template.
        description (str): A description of the case template.
        rules: The primary rules associated with the case template.
        secondary_rules: Secondary rules associated with the case template.
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
        """
        Returns the natural key for the case template.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)
    
    def __str__(self):
        """
        String representation of the case template.

        Returns:
            str: The name of the case template.
        """
        return str(self.name)
    
    def get_rules(self):
        """
        Retrieves all primary rules associated with the case template.

        Returns:
            A queryset of primary rules.
        """
        return self.rules.all()
        
    
    def get_secondary_rules(self):
        """
        Retrieves all secondary rules associated with the case template.

        Returns:
            QuerySet: A queryset of secondary rules.
        """
        rules = self.secondary_rules.all()
        return rules
    
    def _assert_max_one_create_patient_rule(self):
        """
        Ensures that there is at most one rule with rule_type "create-object" and target_model "Patient".

        Raises:
            ValueError: If more than one rule of the specified type exists.
        """
        create_patient_rules = self.rules.filter(rule_type__name="create-object", target_model__name="Patient")
        if len(create_patient_rules) > 1:
            raise ValueError(
                "There can be at most one rule with the rule_type__name 'create-object' and target_model__name 'Patient'."
            )
        
    # custom save method which runs the _assert_max_one_create_patient_rule method and others
    def save(self, *args, **kwargs):
        # self._assert_max_one_create_patient_rule() #TODO Fails on first save since many to many can only be used if object has an id
        super().save(*args, **kwargs)

    def get_create_patient_rule(self):
        """
        Retrieves the "create-patient" rule.

        Returns:
            CaseTemplateRule: The rule that creates a patient.

        Raises:
            ValueError: If multiple such rules exist.
        """
        from .case_template_rule import CaseTemplateRule
        create_patient_rules = self.rules.filter(rule_type__name="create-object", target_model="Patient")
        if len(create_patient_rules) > 1:
            raise ValueError("There can be at most one rule with the rule_type__name 'create-object' and target_model__name 'Patient'.")
        elif len(create_patient_rules) == 0:
            create_patient_rules = CaseTemplateRule.objects.get_default_create_patient_rule()
        return create_patient_rules[0]
    
    def get_create_patient_medication_schedule_rule(self):
        """
        Retrieves the "create-patient_medication_schedule" rule.

        Returns:
            CaseTemplateRule: The rule for creating a patient medication schedule.

        Raises:
            ValueError: If multiple such rules exist.
        """
        from .case_template_rule import CaseTemplateRule
        create_patient_medication_schedule_rules = self.rules.filter(rule_type__name="create-object", target_model="PatientMedicationSchedule")
        if len(create_patient_medication_schedule_rules) > 1:
            raise ValueError("There can be at most one rule with the rule_type__name 'create-object' and target_model__name 'PatientMedicationSchedule'.")
        elif len(create_patient_medication_schedule_rules) == 0:
            from warnings import warn
            warn("No create patient medication schedule rule found. Using default create patient medication schedule rule.")
            create_patient_medication_schedule_rules = CaseTemplateRule.objects.get_default_create_patient_medication_schedule_rule()
        return create_patient_medication_schedule_rules[0]
