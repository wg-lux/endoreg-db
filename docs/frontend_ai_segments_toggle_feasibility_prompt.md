# Prompt: Machbarkeitsstudie für Frontend-Schalter "KI-Segmente anzeigen"

Bitte erstelle eine **Machbarkeitsstudie** für einen Frontend-Schalter, der in der Video-/Timeline-Ansicht zwischen **manuellen Segmenten** und **KI-basierten Segmenten** umschalten bzw. filtern kann.

## Kontext

- Backend: `endoreg_db` (Django + DRF)
- Relevante Segmente kommen aus:
  - manueller Erstellung über `segments_crud`
  - KI-Pipeline (`pipe_1`) mit Prediction-Segmenten
- Es existiert jetzt zusätzlich eine dedizierte Route für **redundante KI-segmentbasierte Frame-Annotations** mit eigener `information_source`:
  - `prediction_annotation`
- Wichtig: KI-Annotationen sollen **parallel** zu manuellen Annotationen existieren und diese **nicht überschreiben**.

## Relevante API-Endpunkte (URL-Contract)

Aus `docs/frontend_agent_url_contract.md`:

- `media.videoSegments(pk)` → `GET /api/media/videos/{pk}/segments/`
- `media.videoSegmentDetail(pk, segmentId)` → `GET/PATCH/DELETE /api/media/videos/{pk}/segments/{segmentId}/`
- `media.ensureSegmentAnnotationsForVideo(pk)` → manuelle/konfigurierbare Segment-Annotationen
- `media.ensurePredictionSegmentAnnotationsForVideo(pk)` → neue KI-segmentbasierte redundante Frame-Annotationen (`prediction_annotation`)

## Ziel der Studie

Bewerte, wie ein UI-Schalter umgesetzt werden kann, der z. B. folgende Modi unterstützt:

1. `Alle Segmente`
2. `Nur manuelle Segmente`
3. `Nur KI-Segmente`

Optional zusätzlich:

1. Anzeige-/Overlay-Schalter für Frame-Annotation-Spuren:
   - `manual_annotation`
   - `prediction`
   - `prediction_annotation`

## Bitte analysiere konkret

### 1. Datenverfügbarkeit im aktuellen Backend-Response

Prüfe, ob der Segment-Response aktuell ausreichend Information liefert, um KI-Segmente sicher zu erkennen.

Insbesondere prüfen:
- `source` / `source_name`
- `prediction_meta` / `prediction_meta_id`
- explizites Flag `is_prediction_segment`

Wenn diese Felder fehlen:
- benenne die Lücke klar
- schlage einen minimalen Serializer-Zusatz vor

### 2. Erkennungslogik für KI-Segmente

Beschreibe eine robuste Heuristik / Ziel-Logik:
- bevorzugt: `prediction_meta != null`
- kompatibel: `source_name == "prediction"`
- wie mit gemischten/legacy Daten umgehen?

### 3. UI/UX-Konzept für den Schalter

Bewerte:
- Platzierung (z. B. Timeline Toolbar)
- Default-Ansicht
- Darstellung, wenn beide Spuren sichtbar sind
- Farbcodierung / Legende
- Verhalten bei leeren Ergebnissen (z. B. keine KI-Segmente vorhanden)

### 4. Technische Implementierung im Frontend

Bitte gib einen konkreten Implementierungsvorschlag:
- State-Modell (z. B. `segment_filter_mode = "all" | "manual" | "ai"`)
- Clientseitiges Filtering vs. serverseitiges Filtering
- Auswirkungen auf bestehende Timeline-/Segment-Komponenten
- Aufwandsschätzung (S/M/L)

### 5. Backend-Erweiterungen (falls empfohlen)

Bitte liste minimal notwendige Backend-Änderungen mit Priorität:
- z. B. `LabelVideoSegmentSerializer` um Felder ergänzen:
  - `source_name`
  - `prediction_meta_id`
  - `is_prediction_segment`

Optional:
- segment-spezifische Route zum Erzeugen von `prediction_annotation`
- Query-Parameter für serverseitiges Segment-Filtering (`?segment_origin=ai|manual|all`)

### 6. Risiken / Edge Cases

Bitte benenne Risiken:
- Legacy-Daten ohne konsistente `source`
- Segmente mit `prediction_meta`, aber manuell validiert/editiert
- doppelte Anzeige durch parallele Annotation-Spuren
- Performance bei großen Videos / vielen Frames

## Erwartetes Ergebnisformat

Bitte liefere:

1. **Kurzfazit** (Machbarkeit: Ja/Nein + Bedingungen)
2. **Ist-Zustand**
3. **Soll-Konzept**
4. **Minimaler Backend-Änderungssatz**
5. **Frontend-Implementierungsplan**
6. **Risiken & Mitigations**
7. **Empfohlene Reihenfolge (1-2 Sprint-Schritte)**

## Wichtige Leitplanken

- Keine camelCase-API-Felder neu einführen (Projektregel: snake_case)
- Lösung muss mit redundanten Annotation-Spuren kompatibel sein
- Manuelle Annotationen dürfen nicht überschrieben werden
- Wenn möglich: zuerst minimal-invasiv (Serializer-Felder), dann UI-Schalter

