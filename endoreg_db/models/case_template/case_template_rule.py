from django.db import models

class CaseTemplateRuleTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplateRuleType(models.Model):
    """
    A class representing a case template rule type.

    Attributes:
        name (str): The name of the case template rule type.
        description (str): A description of the case template rule type.

    """
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)

    objects = CaseTemplateRuleTypeManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name

class CaseTemplateRuleManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
    def get_default_create_patient_rule(self):
        """Get the default create patient rule."""
        return self.get(name="create-patient-default")
    
    def get_default_create_patient_lab_sample_rules(self):
        """Get the default create patient lab sample rules."""
        return self.get(name="create-patient_lab_sample-default")
    
    def get_default_create_patient_medication_schedule_rule(self):
        """Get the create patient medication schedule rule."""
        r = self.get(name="create-patient_medication_schedule")
        assert r, "No create patient medication schedule rule found"
        return r
    
class CaseTemplateRule(models.Model):
    """
    A class representing a case template rule.

    Attributes:
        name (str): The name of the case template rule.
        case_template (CaseTemplate): The case template to which the rule belongs.
        description (str): A description of the case template rule.
        name_de (str): The name of the case template rule in German.
        name_en (str): The name of the case template rule in English.
        rule_type (CaseTemplateRuleType): The type of the rule.
        parent_model (str): The model on which the rule is applied.
        target_field (str): The field of the parent model on which the rule is applied.
        target_model (str): The model to which the foreign key points (used for foreign key and many-to-many rules).
        rule_values (list): A list of string values for the rule.
        extra_parameters (dict): A dictionary of extra parameters for the rule.
        value_type (CaseTemplateRuleValueType): The type of the rule value.
        chained_rules (QuerySet): The chained rules associated with the current rule.
        single_categorical_value_distribution (SingleCategoricalValueDistribution): The single categorical value distribution for the rule.
        numerical_value_distribution (NumericValueDistribution): The numerical value distribution for the rule.
        multiple_categorical_value_distribution (MultipleCategoricalValueDistribution): The multiple categorical value distribution for the rule.
        date_value_distribution (DateValueDistribution): The date value distribution for the rule.
        objects (CaseTemplateRuleManager): The manager for the CaseTemplateRule model.

    Methods:
        natural_key(): Returns the natural key of the rule.
        __str__(): Returns a string representation of the rule.
        get_rule_type(): Returns the name of the rule type.
        get_chained_rules(): Returns all the chained rules associated with the current rule.
        get_all_downward_chained_rules(): Returns all the chained rules of the current rule, including indirectly chained rules.
        chained_rules_has_self_reference(): Checks if any directly or indirectly chained rules reference this rule, creating a non-terminating loop.
        save(*args, **kwargs): Customizes the save method to check for self-references.

    """
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)
    rule_type = models.ForeignKey(
        "CaseTemplateRuleType", on_delete=models.CASCADE
    )
    parent_model = models.CharField(max_length=255, blank=True, null=True)
    parent_field = models.CharField(max_length=255, blank=True, null=True)
    target_field = models.CharField(max_length=255, blank=True, null=True)
    target_model = models.CharField(max_length=255, blank=True, null=True)
    rule_values = models.JSONField(blank=True, null=True)
    extra_parameters = models.JSONField(blank=True, null=True)
    value_type = models.ForeignKey("CaseTemplateRuleValueType", on_delete=models.SET_NULL, null=True)
    chained_rules = models.ManyToManyField(
        "CaseTemplateRule",
        related_name="calling_rules"
    )
    single_categorical_value_distribution = models.ForeignKey(
        "SingleCategoricalValueDistribution", on_delete=models.SET_NULL, null=True
    )
    numerical_value_distribution = models.ForeignKey(
        "NumericValueDistribution", on_delete=models.SET_NULL, null=True
    )
    multiple_categorical_value_distribution = models.ForeignKey(
        "MultipleCategoricalValueDistribution", on_delete=models.SET_NULL, null=True
    )
    date_value_distribution = models.ForeignKey(
        "DateValueDistribution", on_delete=models.SET_NULL, null=True
    )
    objects = CaseTemplateRuleManager()

    def natural_key(self):
        """
        Returns the natural key of the rule.

        :return: A tuple representing the natural key of the rule.
        """
        return (self.name,)

    def __str__(self):
        """
        Returns a string representation of the rule.

        :return: A string representation of the rule.
        """
        return self.name

    def get_rule_type(self):
        """
        Returns the name of the rule type.

        :return: The name of the rule type.
        """
        return self.rule_type.name

    def get_chained_rules(self):
        """
        Returns all the chained rules associated with the current rule.

        :return: A queryset of all the chained rules associated with the current rule.
        """
        return self.chained_rules.all()
    
    def get_distribution(self):
        """
        Returns the value distribution of the rule.

        :return: The value distribution of the rule.
        """
        DEBUG = True
        if DEBUG:
            print("Rule: ", self.name)
            print(f"single_categorical_value_distribution: {self.single_categorical_value_distribution}")
            print(f"numerical_value_distribution: {self.numerical_value_distribution}")
            print(f"multiple_categorical_value_distribution: {self.multiple_categorical_value_distribution}")
            print(f"date_value_distribution: {self.date_value_distribution}")


        if self.single_categorical_value_distribution:
            return self.single_categorical_value_distribution
        elif self.numerical_value_distribution:
            return self.numerical_value_distribution
        elif self.multiple_categorical_value_distribution:
            return self.multiple_categorical_value_distribution
        elif self.date_value_distribution:
            return self.date_value_distribution
        return None

    def get_all_downward_chained_rules(self):
        """
        Get all chained rules of the current rule, including indirectly chained rules.

        :return: A set of all chained rules of the current rule.
        """
        all_chained_rules = set()

        def traverse_chained_rules(rule):
            """
            Helper function to recursively traverse chained rules.

            :param rule: The current CaseTemplateRule being checked.
            :return: None
            """
            for chained_rule in rule.chained_rules.all():
                if chained_rule not in all_chained_rules:
                    all_chained_rules.add(chained_rule)
                    traverse_chained_rules(chained_rule)

        # Initialize the traversal starting with the current rule
        traverse_chained_rules(self)
        return all_chained_rules

    def chained_rules_has_self_reference(self):
        """
        Check if any directly or indirectly chained rules reference this rule, creating a non-terminating loop.
        Return a list containing a tuple (first_rule, self_referencing_rule) for each self-reference.
        first_rule is the most upward rule in the chain that references the rule.
        self_referencing_rule is the rule that references the current rule itself.

        :return: A list of tuples representing the self-referencing rules.
        """
        result_list = []

        def traverse_chained_rules(rule, visited_rules):
            """
            Helper function to recursively traverse chained rules and check for self-references.

            :param rule: The current CaseTemplateRule being checked.
            :param visited_rules: A list of tuples representing the path of rules visited so far.
            :return: None
            """
            for chained_rule in rule.chained_rules.all():
                if chained_rule == self:
                    # A self-reference is detected
                    first_rule = visited_rules[0][0]
                    self_referencing_rule = visited_rules[-1][1]
                    result_list.append((first_rule, self_referencing_rule))
                elif chained_rule not in [r[1] for r in visited_rules]:
                    # Continue to check chained rules of the current chained rule
                    traverse_chained_rules(chained_rule, visited_rules + [(rule, chained_rule)])

        # Initialize the traversal starting with the current rule
        traverse_chained_rules(self, [(self, self)])
        return result_list
    
    def get_target_model(self):
        """
        Returns the target model of the rule.

        :return: The target model of the rule.
        """
        from django.apps import apps

        if not self.target_model:
            return None

        try:
            target_model = apps.get_model("endoreg_db", self.target_model)
        except LookupError:
            raise ValueError(f"Model {self.target_model} not found.")
        return target_model
    
    def get_target_field(self):
        """
        Returns the target field of the rule.

        :return: The target field of the rule.
        """
        return self.target_field
    
    def get_parent_model(self):
        """
        Returns the parent model of the rule.

        :return: The parent model of the rule.
        """
        from django.apps import apps

        if not self.parent_model:
            return None

        try:
            self.parent_model = apps.get_model("endoreg_db", self.parent_model)
        except LookupError:
            raise ValueError(f"Model {self.parent_model} not found.")
        return self.parent_model

    # customize the save method to check for self-references
    # def save(self, *args, **kwargs):
    #     # Check for self-references
    #     self_referencing_rules = self.chained_rules_has_self_reference()
    #     if self_referencing_rules:
    #         raise ValueError(f"Self-references detected: {self_referencing_rules}")
    #     super().save(*args, **kwargs)

