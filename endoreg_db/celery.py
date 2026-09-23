from __future__ import annotations

import os
from typing import Protocol, cast

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "endoreg_db.config.settings.prod")


class CeleryApplication(Protocol):
    def config_from_object(self, obj: str, *, namespace: str) -> None: ...

    def autodiscover_tasks(self) -> None: ...


app = cast(CeleryApplication, Celery("endoreg_db"))
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
