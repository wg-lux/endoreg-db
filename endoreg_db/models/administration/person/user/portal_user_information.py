from django.db import models
from typing import TYPE_CHECKING
# models.py in your main app

if TYPE_CHECKING:
    from ..profession import Profession
    from django.contrib.auth.models import User


class PortalUserInfo(models.Model):
    user = models.OneToOneField("auth.User", on_delete=models.CASCADE)
    profession = models.ForeignKey('endoreg_db.Profession', on_delete=models.CASCADE, blank=True, null=True)
    works_in_endoscopy = models.BooleanField(blank=True, null=True)
    # Add other fields as needed

    if TYPE_CHECKING:
        user: "User"
        profession: "Profession"

    def __str__(self):
        return self.user.username
