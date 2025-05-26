# stats/services.py
from django.db.models import Count
from endoreg_db.models.state.audit_ledger import AuditLedger

def _distinct(object_type: str, action: str):
    return (
        AuditLedger.objects
        .filter(object_type=object_type, action=action)
        .values('object_pk')
        .distinct()
        .count()
    )

def collect_counters():
    return {
        "totalCases":           _distinct("VideoFile", "created"),
        "totalVideos":          _distinct("VideoFile", "created"),
        "totalAnnotations":     AuditLedger.objects.filter(action="annotation_added").count(),
        "totalAnonymizations":  _distinct("VideoFile", "anonymized"),
        "totalImages":          _distinct("Image",     "created"),
        "videosCompleted":      _distinct("VideoFile", "validated"),
        "videosAnonym":         _distinct("VideoFile", "anonymized"),
    }
