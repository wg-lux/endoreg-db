'''
---------------------------------------------------------------------------------------
CLASSIFICATION API ENDPOINTS

Diese Endpunkte führen automatische Polyp-Klassifikationen durch:
- NICE: Für digitale Chromoendoskopie/NBI-basierte Klassifikation
- PARIS: Für Standard-Weißlicht-Klassifikation

Diese APIs sind für Backend-Verarbeitung gedacht und werden typischerweise
nach dem Import eines Videos automatisch aufgerufen.
---------------------------------------------------------------------------------------
'''

from django.urls import path

url_patterns = [
    # NICE Classification API
    # POST /api/classifications/nice/
    # Body: {"video_ids": [1, 2, 3]} oder leerer Body für alle Videos
    # Führt NICE-Klassifikation für spezifizierte Videos durch
    # path('classifications/nice/', ForNiceClassificationView.as_view(), name='nice_classification'),
    
    # PARIS Classification API  
    # POST /api/classifications/paris/
    # Body: {"video_ids": [1, 2, 3]} oder leerer Body für alle Videos
    # Führt PARIS-Klassifikation für spezifizierte Videos durch
    # path('classifications/paris/', ForParisClassificationView.as_view(), name='paris_classification'),
    
    # Batch Classification API (beide Typen)
    # POST /api/classifications/batch/
    # Body: {"video_ids": [1, 2, 3], "types": ["nice", "paris"]}
    # Führt beide Klassifikationstypen für spezifizierte Videos durch
    # path('classifications/batch/', BatchClassificationView.as_view(), name='batch_classification'),
    
    # Classification Status API
    # GET /api/classifications/status/<video_id>/
    # Gibt den Status der Klassifikationen für ein Video zurück
    # path('classifications/status/<int:video_id>/', ClassificationStatusView.as_view(), name='classification_status'),
]