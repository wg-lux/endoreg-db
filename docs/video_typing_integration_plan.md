# Video Typing Integration Plan

> Die Statusverfolgung wurde nach `feature-tracking/TypeSafety.yml` migriert.
> Dieses Dokument bleibt als technischer Kontext erhalten und führt keinen
> unabhängigen Fertigstellungsstatus mehr.

## Ziel

Pruefen und schrittweise umsetzen, wie `endoreg-db` in der Videoverarbeitung vom staerkeren Typing in `lx-dtypes` profitieren kann, ohne die bestehenden Django-Modelle direkt zu ersetzen.

Der sinnvolle Ansatz ist eine typed boundary:

- Django ORM bleibt Source of Truth
- `lx-dtypes` liefert strenge Pydantic-Contracts fuer Export, API-Grenzen und interne Normalisierung

## Aktueller Befund

### 1. Videozustand

`endoreg-db` modelliert Videozustand ueber viele Bool-Felder:

- `endoreg_db/models/state/video.py`

Dort existiert bereits eine abgeleitete Enum-Sicht:

- `VideoState.anonymization_status`

`lx-dtypes` hat dafuer einen kanonischen Typ:

- `lx-data-models/lx_dtypes/models/ledger/p_video/state.py`

Nutzen:

- weniger implizite Bool-Kombinationen
- stabilere API-Filter
- klarere Job-Orchestrierung

### 2. Videosegmente

`endoreg-db` nutzt:

- `endoreg_db/models/label/label_video_segment/label_video_segment.py`
- `endoreg_db/serializers/label_video_segment/label_video_segment.py`
- `endoreg_db/services/segment_sync.py`

`lx-dtypes` bietet:

- `lx-data-models/lx_dtypes/models/ledger/p_video_segment/Pydantic.py`
- `lx-data-models/lx_dtypes/models/ledger/p_video_segment/DataDict.py`

Nutzen:

- klarer Contract fuer Segmentdaten
- weniger lose `dict[str, Any]`-Pfade
- konsistente Validierung fuer Create/Update/Export

Wichtige Abweichung:

- `lx-dtypes.PVideoSegment` verlangt `label` und `labelset`
- `endoreg-db` erlaubt heute Segmente ohne Label
- `labelset` ist in `endoreg-db` oft nur indirekt ableitbar

Fazit:

- kein direkter 1:1-Ersatz moeglich
- zuerst Adapter und explizite labelset-Aufloesung noetig

### 3. Sensitive Meta

`endoreg-db`:

- `endoreg_db/models/metadata/sensitive_meta.py`

`lx-dtypes`:

- `lx-data-models/lx_dtypes/models/meta/SensitiveMeta.py`

Nutzen:

- robuster Exportvertrag
- bessere Datenqualitaet
- gemeinsame typed Sicht fuer Video und Reportpfade

Abweichungen:

- `endoreg-db` hat FKs und Django-Beziehungen
- `lx-dtypes` ist flacher und exportorientiert

Fazit:

- Adapter ist sinnvoll
- direkte Modellangleichung waere spaeter und deutlich invasiver

## Konkreter Plan

### Phase 1: Typed Boundary vorbereiten

1. Zentralen Adapter `VideoFile -> lx_dtypes.PatientVideoFile` einfuehren
2. Zentralen Adapter `SensitiveMeta -> lx_dtypes.SensitiveMeta` einfuehren
3. Enum-Mapping fuer Videozustand auf `lx_dtypes.AnonymizationState` vereinheitlichen

Ergebnis:

- exportierbare, validierte Pydantic-Sicht auf Videos
- kein Eingriff in bestehende DB-Modelle

### Phase 2: Segment-Contracts stabilisieren

1. Zentrale Funktion fuer `segment -> labelset_name` definieren
2. Typed Inputmodelle fuer Segment-Create/Update an API-Grenzen einfuehren
3. `segment_sync.py` von losem `Dict[str, Any]` auf typed Eingaben umstellen

Ergebnis:

- weniger implizite Annahmen
- weniger fehleranfaellige Request-Normalisierung

### Phase 3: Exportpfade umstellen

1. Video-/Segment-Exporte gegen die typed Adapter laufen lassen
2. Exportstrukturen an `lx-dtypes`-Contracts ausrichten
3. Validierung beim Export verpflichtend machen

Ergebnis:

- stabile Datenausgabe
- fruehe Fehlererkennung bei unvollstaendigen Daten

### Phase 4: Optional spaetere Modellangleichung

Nur wenn Phase 1-3 sich bewaehren:

1. Pruefen, ob DB-Modelle schrittweise strenger werden sollen
2. Optional `labelset` am Segment explizit persistieren
3. Optional Statusfelder weiter normalisieren

## Empfohlene erste Umsetzungsschritte

### Schritt A

Neue Adapterdatei einfuehren, z. B.:

- `endoreg_db/services/lx_video_contracts.py`

Inhalt:

- `build_lx_sensitive_meta(...)`
- `build_lx_p_video_segment(...)`
- `build_lx_patient_video_file(...)`

### Schritt B

Status-Mapping zentralisieren:

- bestehende Bool-Logik in `VideoState` weiter nutzen
- API und Exporte nur noch den Enum-Status verwenden

### Schritt C

Gezielte Tests bauen:

- Adaptertests ohne API
- Segment-Validierungstests
- Exporttests fuer Videos mit und ohne SensitiveMeta

## Risiken

1. `labelset` ist in `endoreg-db` nicht ueberall explizit vorhanden
2. manuelle Segmente und Prediction-Segmente haben unterschiedliche Informationsdichte
3. Django-FKs und `lx-dtypes`-Flachmodelle bilden nicht dieselbe Struktur ab
4. direkte Modellmigration waere aktuell zu riskant fuer den ersten Schritt

## Empfehlung

Nicht mit DB-Migrationen anfangen.

Zuerst:

- Adapter
- Enum-Status-Normalisierung
- typed Segment-Inputmodelle
- Exportvalidierung

Das bringt den groessten Nutzen bei geringstem Risiko.

## Implementierter kanonischer Boundary-Pfad

`endoreg_db.services.lx_video_contracts` ist der kanonische Adapter für
`VideoFile`, `LabelVideoSegment`, `VideoState` und `SensitiveMeta`. Der
AI-Dataset-Export verwendet dieselben Adapter.

- Django-Primärschlüssel für Sensitive Meta und Segmente werden deterministisch
  auf stabile Contract-UUIDs abgebildet.
- Segmentzustände werden aus dem persistierten Zustand normalisiert; manuelle
  Segmente werden nicht als Prediction ausgegeben.
- Fehlende Labels, nicht auflösbare Labelsets, fehlende Zustände, unbekannte
  Anonymisierungswerte und widersprüchliche Datumsangaben schlagen laut fehl.
- Ungültige Segmente werden nicht aus einem Export übersprungen.
- Der Video-Contract referenziert ausschließlich den verarbeiteten Dateipfad;
  der Adapter exportiert keine Raw-Media-Referenz.
- Der AI-Dataset-Artefaktpfad entfernt `PatientVideoFile.sensitive_meta` zentral
  bei der JSON-Serialisierung. Direkte Identifikatoren, Geburtsdatum,
  Fallnummer, externe ID und Rohtext verlassen diese Boundary nicht;
  unbekannte Top-Level-Felder weist das strikte Exportmodell ab.
