# Upload API Documentation

## Überblick

Die Upload API ermöglicht es, PDF- und Videodateien hochzuladen und asynchron zu verarbeiten. Die API unterstützt:

- **PDF-Dateien**: Automatische Extraktion von Metadaten mit ReportReader
- **Video-Dateien**: Verarbeitung über die bestehende VideoFile-Pipeline
- **Asynchrone Verarbeitung**: Mit Celery für Hintergrundverarbeitung
- **Status-Polling**: Echtzeitüberwachung des Verarbeitungsfortschritts

## API-Endpunkte

### 1. Datei-Upload

**Endpunkt:** `POST /api/upload/`

**Beschreibung:** Lädt eine PDF- oder Videodatei hoch und startet die asynchrone Verarbeitung.

**Request Format:**
```http
POST /api/upload/
Content-Type: multipart/form-data

file: <binary_file_data>
```

**Unterstützte Dateiformate:**
- **PDF**: `application/pdf`
- **Videos**: `video/mp4`, `video/avi`, `video/quicktime`, `video/x-msvideo`, `video/x-ms-wmv`

**Dateigrößenlimit:** 1 GB

**Response (201 Created):**
```json
{
    "upload_id": "123e4567-e89b-12d3-a456-426614174000",
    "status_url": "/api/upload/123e4567-e89b-12d3-a456-426614174000/status/"
}
```

**Fehlercodes:**
- `400 Bad Request`: Ungültige Datei, falsche Größe oder fehlende Datei
- `500 Internal Server Error`: Server-Fehler beim Erstellen des Upload-Jobs

### 2. Status-Abfrage

**Endpunkt:** `GET /api/upload/{upload_id}/status/`

**Beschreibung:** Gibt den aktuellen Verarbeitungsstatus eines Upload-Jobs zurück.

**Parameter:**
- `upload_id`: UUID des Upload-Jobs

**Response (200 OK):**

*Pending/Processing:*
```json
{
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "status": "processing"
}
```

*Erfolgreich abgeschlossen:*
```json
{
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "status": "anonymized",
    "sensitive_meta_id": 42
}
```

*Fehler:*
```json
{
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "status": "error",
    "error_detail": "Processing failed: Invalid PDF format"
}
```

**Status-Werte:**
- `pending`: Upload erhalten, wartet auf Verarbeitung
- `processing`: Datei wird verarbeitet
- `anonymized`: Verarbeitung erfolgreich abgeschlossen
- `error`: Fehler bei der Verarbeitung

**Fehlercodes:**
- `404 Not Found`: Upload-Job nicht gefunden

## Frontend-Integration

### JavaScript-Beispiel

```javascript
class UploadManager {
    constructor() {
        this.pollingInterval = 2000; // 2 Sekunden
    }

    async uploadFile(file) {
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch('/api/upload/', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Upload failed');
            }

            const result = await response.json();
            return result;
        } catch (error) {
            console.error('Upload error:', error);
            throw error;
        }
    }

    async pollStatus(uploadId, onStatusUpdate, onComplete, onError) {
        const poll = async () => {
            try {
                const response = await fetch(`/api/upload/${uploadId}/status/`);
                
                if (!response.ok) {
                    throw new Error('Status check failed');
                }

                const status = await response.json();
                onStatusUpdate(status);

                if (status.status === 'anonymized') {
                    onComplete(status);
                } else if (status.status === 'error') {
                    onError(status);
                } else {
                    // Continue polling
                    setTimeout(poll, this.pollingInterval);
                }
            } catch (error) {
                onError({ error: error.message });
            }
        };

        poll();
    }

    async uploadWithProgress(file, callbacks = {}) {
        const {
            onUploadStart = () => {},
            onUploadComplete = () => {},
            onStatusUpdate = () => {},
            onProcessingComplete = () => {},
            onError = () => {}
        } = callbacks;

        try {
            onUploadStart();
            
            // Upload file
            const uploadResult = await this.uploadFile(file);
            onUploadComplete(uploadResult);

            // Start polling
            this.pollStatus(
                uploadResult.upload_id,
                onStatusUpdate,
                onProcessingComplete,
                onError
            );

        } catch (error) {
            onError({ error: error.message });
        }
    }
}

// Verwendung:
const uploadManager = new UploadManager();

const fileInput = document.getElementById('file-input');
fileInput.addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    await uploadManager.uploadWithProgress(file, {
        onUploadStart: () => console.log('Upload gestartet...'),
        onUploadComplete: (result) => console.log('Upload abgeschlossen:', result),
        onStatusUpdate: (status) => console.log('Status:', status.status),
        onProcessingComplete: (result) => {
            console.log('Verarbeitung abgeschlossen!');
            console.log('SensitiveMeta ID:', result.sensitive_meta_id);
        },
        onError: (error) => console.error('Fehler:', error)
    });
});
```

### Vue.js Komponenten-Beispiel

```vue
<template>
  <div class="upload-component">
    <div class="upload-area" @drop="handleDrop" @dragover.prevent>
      <input
        ref="fileInput"
        type="file"
        @change="handleFileSelect"
        accept=".pdf,.mp4,.avi,.mov,.wmv"
        style="display: none"
      />
      <button @click="$refs.fileInput.click()" :disabled="isUploading">
        {{ isUploading ? 'Wird verarbeitet...' : 'Datei auswählen' }}
      </button>
    </div>

    <div v-if="uploadStatus" class="status-display">
      <div class="progress-bar">
        <div 
          class="progress-fill" 
          :style="{ width: progressPercentage + '%' }"
        ></div>
      </div>
      <p>Status: {{ getStatusText(uploadStatus.status) }}</p>
      <p v-if="uploadStatus.error_detail" class="error">
        Fehler: {{ uploadStatus.error_detail }}
      </p>
    </div>
  </div>
</template>

<script>
export default {
  name: 'FileUpload',
  data() {
    return {
      isUploading: false,
      uploadStatus: null,
      pollingTimer: null
    };
  },
  computed: {
    progressPercentage() {
      if (!this.uploadStatus) return 0;
      
      switch (this.uploadStatus.status) {
        case 'pending': return 25;
        case 'processing': return 75;
        case 'anonymized': return 100;
        case 'error': return 100;
        default: return 0;
      }
    }
  },
  methods: {
    async handleFileSelect(event) {
      const file = event.target.files[0];
      if (file) {
        await this.uploadFile(file);
      }
    },

    async handleDrop(event) {
      event.preventDefault();
      const file = event.dataTransfer.files[0];
      if (file) {
        await this.uploadFile(file);
      }
    },

    async uploadFile(file) {
      this.isUploading = true;
      this.uploadStatus = null;

      try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch('/api/upload/', {
          method: 'POST',
          body: formData
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.error || 'Upload failed');
        }

        const result = await response.json();
        this.startPolling(result.upload_id);

      } catch (error) {
        this.uploadStatus = {
          status: 'error',
          error_detail: error.message
        };
        this.isUploading = false;
      }
    },

    startPolling(uploadId) {
      const poll = async () => {
        try {
          const response = await fetch(`/api/upload/${uploadId}/status/`);
          const status = await response.json();
          
          this.uploadStatus = status;

          if (status.status === 'anonymized') {
            this.isUploading = false;
            this.$emit('upload-complete', status);
            clearInterval(this.pollingTimer);
          } else if (status.status === 'error') {
            this.isUploading = false;
            this.$emit('upload-error', status);
            clearInterval(this.pollingTimer);
          }
        } catch (error) {
          console.error('Polling error:', error);
        }
      };

      this.pollingTimer = setInterval(poll, 2000);
      poll(); // Initial call
    },

    getStatusText(status) {
      const statusTexts = {
        'pending': 'Wartet auf Verarbeitung...',
        'processing': 'Wird verarbeitet...',
        'anonymized': 'Erfolgreich verarbeitet',
        'error': 'Fehler bei der Verarbeitung'
      };
      return statusTexts[status] || status;
    }
  },
  beforeUnmount() {
    if (this.pollingTimer) {
      clearInterval(this.pollingTimer);
    }
  }
};
</script>

<style scoped>
.upload-area {
  border: 2px dashed #ccc;
  padding: 2rem;
  text-align: center;
  margin-bottom: 1rem;
}

.progress-bar {
  width: 100%;
  height: 8px;
  background-color: #f0f0f0;
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 1rem;
}

.progress-fill {
  height: 100%;
  background-color: #4CAF50;
  transition: width 0.3s ease;
}

.error {
  color: #f44336;
}
</style>
```

## Backend-Architektur

### Verarbeitungsworkflow

1. **Upload-Empfang**: UploadFileView validiert und speichert die Datei
2. **Job-Erstellung**: UploadJob wird in der Datenbank erstellt
3. **Asynchrone Verarbeitung**: Celery-Task wird gestartet
4. **Dateityp-Routing**: 
   - PDF → ReportReader für Metadatenextraktion
   - Video → VideoFile-Pipeline für Verarbeitung
5. **SensitiveMeta-Erstellung**: Metadaten werden in SensitiveMeta gespeichert
6. **Job-Abschluss**: UploadJob wird als "anonymized" markiert

### Celery-Task Details

Der `process_upload_job` Task behandelt:

- **Fehlerbehandlung**: Umfassende Exception-Behandlung
- **Logging**: Detaillierte Protokollierung für Debugging
- **Rollback**: Fehlerfall-Behandlung mit Status-Updates
- **Flexibilität**: Unterstützung für verschiedene Dateitypen

### Sicherheitsüberlegungen

- **Dateityp-Validierung**: Mehrschichtige MIME-Type-Erkennung
- **Größenbeschränkungen**: Schutz vor übermäßig großen Uploads
- **Sichere Pfade**: Verwendung von Django's File-Storage-System
- **Input-Sanitization**: Validierung aller Eingaben

## Deployment-Konfiguration

### Celery-Setup

```python
# celery.py
from celery import Celery
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'endoreg_db.settings')

app = Celery('endoreg_db')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
```

### Django-Settings

```python
# settings.py

# Celery Configuration
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Europe/Berlin'

# File Upload Settings
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024 * 100  # 100MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024 * 100  # 100MB
```

### Produktionsstart

```bash
# Django-Server
python manage.py runserver

# Celery Worker
celery -A endoreg_db worker -l info

# Celery Beat (für geplante Tasks)
celery -A endoreg_db beat -l info
```

## Tests

Die Upload API wird durch umfassende Tests abgedeckt:

- **Unit Tests**: Model-Funktionalität und Geschäftslogik
- **Integration Tests**: API-Endpunkt-Tests
- **File Processing Tests**: Upload-Workflow-Tests

Tests können mit folgendem Befehl ausgeführt werden:

```bash
python manage.py test tests.test_upload_api
```

## Monitoring und Logs

### Logging-Konfiguration

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'upload_file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'logs/upload.log',
        },
    },
    'loggers': {
        'endoreg_db.tasks.upload_tasks': {
            'handlers': ['upload_file'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}
```

### Überwachung

- **Celery Monitoring**: Flower für Task-Überwachung
- **Performance Metrics**: Task-Ausführungszeiten
- **Error Tracking**: Fehlschläge und Wiederholungen

## Fehlerbehebung

### Häufige Probleme

1. **Celery nicht verfügbar**: System arbeitet im Entwicklungsmodus ohne Hintergrundverarbeitung
2. **Große Dateien**: Anpassung der Upload-Limits in Django und Webserver
3. **Python-magic fehlt**: Fallback auf mimetypes-Modul
4. **Speicherplatz**: Überwachung des Upload-Verzeichnisses