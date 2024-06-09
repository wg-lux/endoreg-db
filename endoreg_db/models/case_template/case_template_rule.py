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
    description = models.TextField(blank=True, null=True)

    objects = CaseTemplateRuleTypeManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name

class CaseTemplateRuleManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplateRule(models.Model):
    """
    A class representing a case template rule.

    Attributes:
        name (str): The name of the case template rule.
        case_template (CaseTemplate): The case template to which the rule belongs.
        description (str): A description of the case template rule.

    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    rule_type = models.ForeignKey("CaseTemplateRuleType", on_delete=models.CASCADE)
    chained_rules = models.ManyToManyField(
        "CaseTemplateRule",
        related_name="chained_rules"
    )
    value_type = models.CharField(max_length=255, blank=True, null=True) #
    # value_set 

    objects = CaseTemplateRuleManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name

    def get_rule_type(self):
        return self.rule_type.name
    
    def get_chained_rules(self):
        return self.chained_rules.all()
    
    def chained_rules_has_self_reference(self):
        """
        Check if any directly or indirectly chained rules reference this rule, creating a non-terminating loop.
        Return a list containing a tuple (first_rule, self_referencing_rule) for each self-reference.
        first_rule is the most upward rule in the chain that references the rule.
        self_referencing_rule is the rule that references the current rule itself.
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
    
    # customize the save method to check for self-references
    def save(self, *args, **kwargs):
        # Check for self-references
        self_referencing_rules = self.chained_rules_has_self_reference()
        if self_referencing_rules:
            raise ValueError(f"Self-references detected: {self_referencing_rules}")
        super().save(*args, **kwargs)

