from django.db import models

class QuizAnswerTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class QuizAnswerType(models.Model):
    objects = QuizAnswerTypeManager()

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

class QuizAnswerManager(models.Manager):
    def get_by_natural_key(self, text):
        return self.get(text=text)
    
class QuizAnswer(models.Model):
    objects = QuizAnswerManager()

    text_value = models.CharField(max_length=255, null=True, blank=True)
    number_value = models.FloatField(null=True, blank=True)
    answer_type = models.ForeignKey("QuizAnswerType", on_delete=models.CASCADE)
    question = models.ForeignKey("QuizQuestion", on_delete=models.CASCADE)

    last_updated = models.DateTimeField(auto_now=True)

    #TODO add user as foreign key

    def natural_key(self):
        return (self.text,)
    
    def __str__(self):
        return self.text
    

