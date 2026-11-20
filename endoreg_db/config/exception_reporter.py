"""Keep injected identity material out of Django error reports."""

import re

from django.views.debug import SafeExceptionReporterFilter


class IdentitySecretExceptionReporterFilter(SafeExceptionReporterFilter):
    hidden_settings = re.compile(
        SafeExceptionReporterFilter.hidden_settings.pattern + "|SALT", re.IGNORECASE
    )
