# Frame-Anonymisierung

Dieses Modul bietet umfassende Funktionalität zur Anonymisierung von Video-Frames basierend auf definierten Segmenten. Es unterstützt verschiedene Anonymisierungsstufen und Ausgabeformate.

## Überblick

Die Frame-Anonymisierung ermöglicht es, sensible Daten in Video-Frames automatisch zu anonymisieren, um Datenschutzbestimmungen einzuhalten. Das System arbeitet segment-basiert und bietet flexible Konfigurationsmöglichkeiten.

### Hauptfunktionen

- **Segment-basierte Anonymisierung**: Anonymisierung nur für definierte Video-Segmente
- **Multiple Anonymisierungsstufen**: Von minimal bis vollständig
- **Verschiedene Ausgabeformate**: JPG, PNG, WebP
- **Asynchrone Verarbeitung**: Celery-basierte Background-Tasks
- **Batch-Verarbeitung**: Mehrere Videos gleichzeitig verarbeiten
- **Download-Management**: Sichere Downloads mit temporären Tokens
- **Comprehensive Logging**: Detaillierte Protokollierung aller Operationen

## API-Endpunkte

### 1. Anonymisierung starten

```http
POST /api/frames/anonymize/
Content-Type: application/json

{
  "video_id": 123,
  "segment_ids": [1, 2, 3],
  "anonymization_level": "faces",
  "output_format": "jpg"
}
```

**Parameter:**
- `video_id` (int): ID des Videos
- `segment_ids` (array): Liste der Segment-IDs
- `anonymization_level` (string): "minimal", "faces", oder "full"
- `output_format` (string): "jpg", "jpeg", "png", oder "webp"

**Response:**
```json
{
  "success": true,
  "request_id": "uuid-string",
  "message": "Frame anonymization started",
  "estimated_processing_time": 120
}
```

### 2. Status abfragen

```http
GET /api/frames/anonymize/status/{request_id}/
```

**Response:**
```json
{
  "request_id": "uuid-string",
  "status": "processing",
  "progress": 65,
  "total_frames": 150,
  "processed_frames": 98,
  "estimated_remaining_time": 45,
  "created_at": "2025-06-30T10:00:00Z",
  "started_at": "2025-06-30T10:01:00Z"
}
```

### 3. Ergebnisse herunterladen

```http
GET /api/frames/anonymize/download/{request_id}/
```

Generiert einen temporären Download-Token und startet den Download der anonymisierten Frames als ZIP-Archiv.

### 4. Request-Liste abrufen

```http
GET /api/frames/anonymize/requests/
```

**Parameter (optional):**
- `video_id`: Filtern nach Video-ID
- `status`: Filtern nach Status
- `limit`: Anzahl Ergebnisse (Standard: 20)
- `offset`: Offset für Paginierung

### 5. Request abbrechen

```http
DELETE /api/frames/anonymize/{request_id}/
```

## Management Commands

### Grundlegende Nutzung

```bash
# Einzelne Anonymisierung
python manage.py anonymize_frames --video-id 123 --segment-ids "1,2,3" --anonymization-level faces

# Asynchrone Verarbeitung
python manage.py anonymize_frames --video-id 123 --segment-ids "1,2,3" --async

# Status prüfen
python manage.py anonymize_frames --status uuid-string

# Alle Requests auflisten
python manage.py anonymize_frames --list-requests

# Statistiken anzeigen
python manage.py anonymize_frames --statistics

# Aufräumen abgelaufener Tokens
python manage.py anonymize_frames --cleanup-expired
```

### Batch-Verarbeitung

```bash
# Batch-Datei verwenden
python manage.py anonymize_frames --batch-file examples/frame_anonymization_batch.json
```

Beispiel-Batch-Datei (`examples/frame_anonymization_batch.json`):
```json
[
  {
    "video_id": 1,
    "segment_ids": "1,2,3",
    "anonymization_level": "faces",
    "output_format": "jpg",
    "async": true
  },
  {
    "video_id": 2,
    "segment_ids": "4,5",
    "anonymization_level": "full",
    "output_format": "png"
  }
]
```

## Anonymisierungsstufen

### Minimal
- Entfernt nur explizit markierte sensible Bereiche
- Schnellste Verarbeitung
- Geringste Bildqualitätsverluste

### Faces (Standard)
- Erkennt und anonymisiert Gesichter automatisch
- Ausgewogenes Verhältnis zwischen Datenschutz und Bildqualität
- Empfohlen für die meisten Anwendungsfälle

### Full
- Umfassende Anonymisierung aller potentiell identifizierbaren Elemente
- Gesichter, Kennzeichen, Text, etc.
- Höchster Datenschutz, längste Verarbeitungszeit

## Ausgabeformate

- **JPG/JPEG**: Standard-Format, gute Kompression
- **PNG**: Verlustfrei, größere Dateien
- **WebP**: Moderne Kompression, kleinere Dateien

## Konfiguration

### Django Settings

```python
# settings.py

# Frame-Anonymisierung Einstellungen
FRAME_ANONYMIZATION = {
    'MAX_CONCURRENT_REQUESTS': 5,
    'DEFAULT_ANONYMIZATION_LEVEL': 'faces',
    'DEFAULT_OUTPUT_FORMAT': 'jpg',
    'DOWNLOAD_TOKEN_EXPIRY_HOURS': 24,
    'MAX_FRAMES_PER_REQUEST': 10000,
    'TEMP_STORAGE_PATH': '/tmp/anonymized_frames',
    'CLEANUP_INTERVAL_HOURS': 6,
}

# Celery-Konfiguration für asynchrone Verarbeitung
CELERY_TASK_ROUTES = {
    'endoreg_db.tasks.frame_anonymization_tasks.process_frame_anonymization': {'queue': 'anonymization'},
    'endoreg_db.tasks.frame_anonymization_tasks.cleanup_anonymization_data': {'queue': 'cleanup'},
}
```

### Celery Worker

Für die asynchrone Verarbeitung muss ein Celery Worker gestartet werden:

```bash
# Allgemeiner Worker
celery -A endoreg_db worker -l info

# Spezieller Worker für Anonymisierung
celery -A endoreg_db worker -Q anonymization -l info

# Periodic Tasks für Cleanup
celery -A endoreg_db beat -l info
```

## Sicherheit

### Authentifizierung
Alle API-Endpunkte erfordern eine gültige Authentifizierung. Das System unterstützt:
- Session-basierte Authentifizierung
- Token-basierte Authentifizierung
- JWT-Tokens (falls konfiguriert)

### Autorisierung
- Benutzer können nur auf ihre eigenen Anonymisierungsanfragen zugreifen
- Administratoren haben Zugriff auf alle Requests
- Segment-basierte Zugriffskontrolle basiert auf Video-Berechtigungen

### Download-Sicherheit
- Temporäre Download-Tokens mit konfigurierbarer Ablaufzeit
- Einmalige Verwendung von Download-Links
- Automatische Bereinigung abgelaufener Daten

## Überwachung und Logging

### Logging-Konfiguration

```python
LOGGING = {
    'loggers': {
        'endoreg_db.frame_anonymization': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

### Monitoring-Metriken

Das System protokolliert folgende Metriken:
- Anzahl verarbeiteter Requests
- Durchschnittliche Verarbeitungszeit
- Fehlerrate
- Speichernutzung
- Queue-Status

### Health Check

```http
GET /api/frames/anonymize/health/
```

Überprüft den Status des Anonymisierungssystems:
```json
{
  "status": "healthy",
  "celery_workers": 3,
  "pending_requests": 5,
  "average_processing_time": 89.5,
  "storage_usage": "15.2 GB"
}
```

## Fehlerbehebung

### Häufige Probleme

1. **"Video not found"**
   - Überprüfen Sie die Video-ID
   - Stellen Sie sicher, dass das Video existiert und zugänglich ist

2. **"Invalid segments"**
   - Segmente müssen zum angegebenen Video gehören
   - Überprüfen Sie die Segment-IDs

3. **"Processing timeout"**
   - Zu viele Frames in einem Request
   - Reduzieren Sie die Anzahl der Segmente oder verwenden Sie Batch-Processing

4. **"Celery worker not available"**
   - Starten Sie den Celery Worker neu
   - Überprüfen Sie die Celery-Konfiguration

### Debug-Modus

Für detaillierte Fehleranalyse aktivieren Sie den Debug-Modus:

```python
# settings.py
FRAME_ANONYMIZATION_DEBUG = True
```

Dies aktiviert zusätzliche Logging-Ausgaben und speichert Zwischenergebnisse.

## Performance-Optimierung

### Empfohlene Hardware
- Mindestens 8 GB RAM
- SSD-Speicher für temporäre Dateien
- GPU-Unterstützung für bessere Performance (optional)

### Skalierung
- Mehrere Celery Worker für parallele Verarbeitung
- Redis/RabbitMQ als Message Broker
- Separate Queues für verschiedene Prioritäten

### Speicher-Management
- Regelmäßige Bereinigung temporärer Dateien
- Konfigurierbare Aufbewahrungszeiten
- Komprimierung der Ausgabedateien

## API-Beispiele

### Python-Client

```python
import requests

# Anonymisierung starten
response = requests.post('/api/frames/anonymize/', json={
    'video_id': 123,
    'segment_ids': [1, 2, 3],
    'anonymization_level': 'faces',
    'output_format': 'jpg'
})

request_id = response.json()['request_id']

# Status überwachen
while True:
    status_response = requests.get(f'/api/frames/anonymize/status/{request_id}/')
    status = status_response.json()['status']
    
    if status == 'completed':
        # Download starten
        download_response = requests.get(f'/api/frames/anonymize/download/{request_id}/')
        break
    elif status == 'failed':
        print("Anonymization failed")
        break
    
    time.sleep(10)
```

### JavaScript-Client

```javascript
// Anonymisierung starten
const response = await fetch('/api/frames/anonymize/', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken
    },
    body: JSON.stringify({
        video_id: 123,
        segment_ids: [1, 2, 3],
        anonymization_level: 'faces',
        output_format: 'jpg'
    })
});

const { request_id } = await response.json();

// Status überwachen
const checkStatus = async () => {
    const statusResponse = await fetch(`/api/frames/anonymize/status/${request_id}/`);
    const status = await statusResponse.json();
    
    if (status.status === 'completed') {
        // Download-Link generieren
        window.location.href = `/api/frames/anonymize/download/${request_id}/`;
    } else if (status.status === 'processing') {
        setTimeout(checkStatus, 5000);
    }
};

checkStatus();
```