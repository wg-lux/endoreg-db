from typing import TYPE_CHECKING

from django.db import models

# models.py in your main app

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from endoreg_db.models import Examiner

    from ..profession import Profession


class PortalUserInfo(models.Model):
    user = models.OneToOneField("auth.User", on_delete=models.CASCADE)
    profession = models.ForeignKey(
        "endoreg_db.Profession",
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
        user: models.OneToOneField["User"]
        profession: models.ForeignKey["Profession|None"]
        examiner: models.OneToOneField["Examiner|None"]

    def __str__(self):
        return self.user.username
