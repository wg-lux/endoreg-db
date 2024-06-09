from django.db import models

class CaseTemplateManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplate(models.Model):
    """
    A class representing a case template.

    Attributes:
        name (str): The name of the case template.
        case_template_type (CaseTemplateType): The type of the case template.
        description (str): A description of the case template.

    """
    name = models.CharField(max_length=255)
    case_template_type = models.ForeignKey("CaseTemplateType", on_delete=models.CASCADE, related_name="case_templates")
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
