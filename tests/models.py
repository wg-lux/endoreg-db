from django.db import models

class NewTestClass(models.Model):
    name = models.CharField(max_length=255, null=True)
    

    

