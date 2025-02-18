from django.db import models

class QuizQuestionTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class QuizQuestionType(models.Model):
    """
    A class representing a quiz question type.

    Attributes:
        name (str): The name of the quiz question type.
        description (str): A description of the quiz question type.

    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    objects = QuizQuestionTypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
class QuizQuestionManager(models.Manager):
    def get_by_natural_key(self, text):
        return self.get(text=text)
    
class QuizQuestion(models.Model):
    """
    A class representing a quiz question.

    Attributes:
        text (str): The text of the quiz question.
        question_type (QuizQuestionType): The type of the quiz question.
        description (str): A description of the quiz question.

    """
    text = models.TextField()
    description = models.TextField(blank=True, null=True)
    question_type = models.ForeignKey("QuizQuestionType", on_delete=models.CASCADE)

    objects = QuizQuestionManager()

    def natural_key(self):
        return (self.text,)
    
    def __str__(self):
        return self.text
    
    def get_question_type(self):
        return self.question_type.name
