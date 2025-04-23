from django.db import models
from typing import TYPE_CHECKING
# models.py in your main app

if TYPE_CHECKING:
    from ..profession import Profession
    from endoreg_db.models import Examiner
    from django.contrib.auth.models import User


class PortalUserInfo(models.Model):
    user = models.OneToOneField("auth.User", on_delete=models.CASCADE)
    profession = models.ForeignKey(
        'endoreg_db.Profession',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_infos",
    )
    works_in_endoscopy = models.BooleanField(blank=True, null=True)
    # Add other fields as needed

    examiner = models.OneToOneField(
        "endoreg_db.Examiner",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_info",
    )

    if TYPE_CHECKING:
        user: "User"
        profession: "Profession"
        examiner: "Examiner"

    def __str__(self):
        return self.user.username
