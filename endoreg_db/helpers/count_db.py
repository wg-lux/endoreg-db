# endoreg_db/helpers/count_db.py  
from endoreg_db.models.state.audit_ledger import AuditLedger

def _distinct(object_type: str, action: str):
    """
    Returns the count of distinct primary keys for records in AuditLedger filtered by object type and action.
    
    Args:
        object_type: The type of object to filter by.
        action: The action to filter by.
    
    Returns:
        The number of unique object primary keys matching the specified type and action.
    """
    return (
        AuditLedger.objects
        .filter(object_type=object_type, action=action)
        .values('object_pk')
        .distinct()
        .count()
    )

def collect_counters():
    """
    Returns a dictionary with statistical counts for cases, videos, annotations, anonymizations, images, and video statuses.
    
    The returned dictionary includes:
    - "totalCases": Count of distinct created video files.
    - "totalVideos": Count of distinct created video files.
    - "totalAnnotations": Count of audit log entries where an annotation was added.
    - "totalAnonymizations": Count of distinct video files that have been anonymized.
    - "totalImages": Count of distinct created images.
    - "videosCompleted": Count of distinct video files that have been validated.
    - "videosAnonym": Count of distinct video files that have been anonymized.
    """
    return {
        #TODO @maxhild can we remove the totalCases key?
        # "totalCases":           _distinct("VideoFile", "created"),
        "totalVideos":          _distinct("VideoFile", "created"),
        "totalAnnotations":     AuditLedger.objects.filter(action="annotation_added").count(),
        "totalAnonymizations":  _distinct("VideoFile", "anonymized"),
        "totalImages":          _distinct("Image",     "created"),
        "videosCompleted":      _distinct("VideoFile", "validated"),
        "videosAnonym":         _distinct("VideoFile", "anonymized"),
    }
