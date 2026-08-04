# FPS-/PTS-Callsite-Inventar

Stand: 2026-07-29. Dieses Inventar ist die technische Referenz für das Feature
[`video_storage_normalization`](../feature-tracking/VideoStorageNormalization.yml).
Der verbindliche Reife- und Freigabestatus steht ausschließlich in der
Feature-YAML.

## Suchumfang und Auswertung

Die Suche umfasst:

- `endoreg-db/endoreg_db` und die zugehörigen Tests, ohne Migrationen und
  generierte Rust-Stubs;
- `lx-annotate/frontend/src`, einschließlich Frontendtests, aber ohne
  generierte `*.vue.js`-Spiegeldateien;
- `lx-annotate/lx_annotate` und dessen Tests, ohne Migrationen.

Gesucht wurde breit nach `fps`, Frame-/Zeitfeldern, `currentTime`, `seek`,
`timestamp`, `pts`, `normalize-fps`, Multiplikation mit FPS und Division durch
FPS. Die breite Suche ergab 1.579 Treffer in 137 endoreg-db-Dateien, 542 Treffer
in 49 lx-annotate-Frontenddateien und 52 Treffer in 21 lx-annotate-Backend- und
Testdateien. Viele Treffer sind Anzeige-, Audit- oder allgemeine Zeitstempel und
kein Frameidentitätsrisiko; die Tabellen unten enthalten die manuell
klassifizierten Callsites.

Statuslegende:

- **migriert**: verwendet den autoritativen PTS-/Timeline-Vertrag;
- **offen**: kann bei variabler Bildrate oder einer geänderten Videogeneration falsche Grenzen
  erzeugen;
- **bedingt sicher**: korrekt, solange die dokumentierte Vorbedingung gilt;
- **beabsichtigt**: erzeugt ausdrücklich eine neue, versionierte Timeline.

## Verbindliche Regel

FPS ist nur eine Rate. Für die Identität eines klinischen Frames sind
`Frame.frame_number`, `Frame.timestamp`, `timeline_version` und die konkrete
Video-Generation maßgeblich.

- Eine variable Bildrate darf niemals über `time * fps` oder `frame / fps` aufgelöst werden.
- CFR darf die rationale FPS-Abbildung nur als expliziten Fallback verwenden.
- Ein FFmpeg-`fps`-Filter erzeugt eine neue Framefolge. Outputnummern dürfen
  keine Quell-Frame-PKs oder Quell-Frameindizes erben.
- Das Backend ist die autoritative Grenze für Timestamp↔Frame-Konvertierung.
  Frontendwerte aus `HTMLMediaElement.currentTime` bleiben Timestamps, bis das
  Backend sie gegen die veröffentlichte Timeline auflöst.

## Priorität 0: offene Cross-Repository-Risiken

| Repository | Callsite | Befund | Erforderliche Migration |
| --- | --- | --- | --- |
| lx-annotate | `frontend/src/utils/segmentTimeline.ts` und `frontend/src/stores/videoStore.ts` | **migriert:** Der zentrale Frontend-Contract validiert Media-Timestamps ohne FPS-Quantisierung. Bulk-Create und -Update senden ausschließlich `start_time`/`end_time`; alte clientseitige Framefelder werden aus Update-Payloads entfernt. | Die kanonischen Timestamp- und Framegrenzen werden aus der Backend-Antwort übernommen. `segmentTimeline.test.ts` prüft irreguläre Zeitwerte für Create und Update. |
| lx-annotate / endoreg-db | `frontend/src/components/VideoExamination/Timeline.vue:stepFrame`; `media/videos/<pk>/timeline/frame-neighborhood/` | **migriert:** Frame vor/zurück fragt ein begrenztes, backendberechnetes PTS-Fenster ab. Der Endpoint delegiert an den kanonischen PTS-Resolver; es existiert kein `1 / fps`-Fallback im UI. Der Store cached nur autoritativ gelieferte Frame-IDs/PTS. | Fehlende oder inkonsistente Präsentationszeitstempel bei variabler Bildrate liefern 422 und deaktivieren framegenaue Navigation fail-closed. Ein `(video, timestamp)`-Index beschleunigt die Boundary-Suchen; das Endpoint-Budget bleibt unabhängig von der Fenstergröße bei vier Queries. |
| lx-annotate | `frontend/src/components/VideoExamination/Timeline.vue:copySelectedSegment,pasteSegment` | **migriert für Mutationen:** Kopieren und Einfügen erhalten die Timestampdauer unverändert und leiten keine Mindestdauer mehr aus FPS ab. | Die abschließende Boundary-Validierung und Kanonisierung erfolgt über den zentralen Store-Contract und die Backend-Antwort. |
| endoreg-db | `endoreg_db/utils/frame_stream.py` | **Offen:** Decode-Auswahl per `select=eq(n,...)` ist frameidentisch, aber zurückgegebene `FrameSample.timestamp` werden weiterhin als `frame_number / fps` konstruiert. Header und Consumer können dadurch bei variabler Bildrate einen falschen Timestamp erhalten. | Für `VideoFile` den persistierten `Frame.timestamp` verwenden. Pfadbasierte Decoder müssen PTS aus FFmpeg/ffprobe übernehmen oder den Timestamp als nicht autoritativ kennzeichnen; kein nomineller FPS-Fallback bei variabler Bildrate. |

Die offenen Callsites verhindern derzeit noch einen vollständigen
Cross-Repository-Nachweis für Timestamp-genaues Decoding; Segment-Create,
-Update und Frame-Stepping sind frontendseitig migriert.

## Priorität 0: gc-10-Incident bei Processed-HLS für Video 44

Am 28. und 29. Juli 2026 materialisierte der einzelne
`ffmpeg_media`-Worker auf gc-10 Processed-HLS für Video 44 wiederholt
vollständig, verwarf die versuchsspezifische Generation anschließend jedoch
mit:

```text
Output FPS drifted from 49.8549 to 50
```

Sechs fehlgeschlagene Versuche wurden mit derselben Celery-Task erneut
zugestellt. Dadurch blieb diese Arbeit am Anfang der Warteschlange, während 79
weitere Nachrichten nicht vorankamen. Die temporären HLS-Artefakte wurden nach
jedem Fehler bereinigt; der Incident ist daher primär ein
Timeline-Validierungs- und Retry-Ownership-Problem und kein Nachweis für einen
beschädigten Import. Ein Reimport oder eine erneute FPS-Normalisierung ist ohne
abweichende Integritäts- beziehungsweise Provenienzevidenz ausdrücklich nicht
die Standard-Recovery.

Der betroffene Call-Pfad ist:

1. `services/hls_media.py:_run_ffmpeg_hls` normalisiert die entschlüsselte
   Processed-Quelle innerhalb des geschützten Versuchspfads und übergibt
   `normalization_evidence.output` als Referenz für die HLS-Prüfung.
2. `services/video_storage/probes.py:_resolve_frame_rate` bevorzugt
   `avg_frame_rate` gegenüber `r_frame_rate` und reduziert beide Werte auf
   einen einzelnen rationalen FPS-Wert plus eine abgeleitete
   Variable-Frame-Rate-Markierung.
3. `services/video_storage/validation.py:assert_temporal_equivalence` vergleicht
   diesen Wert mit einer allgemeinen relativen Float-Toleranz von `0.001`.
4. Die HLS-Ausgabe wird dadurch bei `49.8549` gegenüber `50` abgelehnt, auch
   wenn die veröffentlichte Quellgeneration laut
   `annotation_fps_resample_v1` nominell eine versionierte
   50-FPS-Constant-Frame-Rate-Timeline sein sollte.
5. Der unerwartete Validierungsfehler wird im produktiven Celery-Vertrag nicht
   als nicht wiederholbarer Profilfehler begrenzt und kann deshalb dieselbe
   teure Materialisierung erneut ausführen.

Vor einer Korrektur muss für Video 44 ohne Änderung des Masters geprüft werden:

- persistierte `annotation_fps_resample_v1`-Provenienz und zugehörige
  Generation;
- rationales `r_frame_rate`, `avg_frame_rate`, Time-Base, Dauer und
  Frameanzahl der Processed-Quelle;
- monotone Präsentationszeitstempel und deren Abstände;
- dieselben Werte der entschlüsselten lokalen HLS-Prüfplaylist;
- vorhandene Segment- und extrahierte Framekoordinaten.

Die Korrektur darf die globale FPS-Toleranz nicht pauschal erhöhen. Sie muss
nominale Rate, Durchschnittsrate und PTS-basierte Timeline typisiert
unterscheiden. Eine nachgewiesene versionierte 50-FPS-CFR-Generation darf nur
dann als HLS-äquivalent gelten, wenn Generation, Frameanzahl, Dauer,
Time-Base/PTS-Abbildung und Segmentgrenzen ebenfalls übereinstimmen. Echte
Frameauslassung, -duplikation, variable Bildrate ohne vollständige PTS oder eine
fremde Generation müssen weiterhin laut fehlschlagen.

Zusätzlich muss ein deterministischer Profil- oder Timeline-Validierungsfehler
als nicht automatisch wiederholbar beziehungsweise streng begrenzt behandelt
werden. Er darf nicht die gesamte FIFO-Warteschlange durch unmittelbare
Redelivery blockieren. Nach Ausrollen und Nachweis der Korrektur wird nur das
Processed-HLS von Video 44 erneut materialisiert; der kanonische Master wird
nicht reimportiert.

## Priorität 0: Annotationsexport

| Callsite | Status | Bewertung |
| --- | --- | --- |
| `endoreg_db/utils/video/command_construction.py:_build_extract_frames_command` | **migriert** | Ohne Samplingrate wird `fps_mode=passthrough` verwendet. Ein expliziter Samplingmodus bleibt nur für unabhängige neue Sequenzen zulässig. |
| `endoreg_db/export/frames/export_frames_with_labels.py:_extract_and_move_transcoded_frames` | **migriert** | Selektierte Annotationen werden über ihren persistierten Quell-Framebereich ohne FPS-Filter extrahiert und vollständig gegen die angeforderten Frame-PKs geprüft. |
| `endoreg_db/export/frames/export_frames_with_labels.py:_move_extracted_frames_to_pk_names` | **migriert** | Der unsichere `frame_number - 1`-Fallback ist entfernt; fehlende Frames brechen den Export ab. |
| `lx-annotate/frontend/src/components/VideoExamination/ExportAnnotations.vue` | **migriert** | Identitätserhaltende Frameexports senden kein `transcode_fps` mehr. `ExportAnnotations.pts-contract.test.ts` schützt diesen Vertrag. |
| `endoreg_db/services/frames/materialize_training_frames.py` und `export_frame_annot` | **bedingt sicher** | Legacy-`transcode_fps` kann noch durchgereicht werden, wird im Annotationsexporter aber nicht zur Framezuordnung verwendet. |

## Priorität 1: endoreg-db – klinische Zeit-/Framekoordinaten

| Callsite | Status | Bewertung oder Restarbeit |
| --- | --- | --- |
| `endoreg_db/services/video_timeline.py` | **migriert** | Reiner, typisierter Mapping-Kern; PTS hat Vorrang, nearest-boundary wählt bei Gleichstand den kleineren Frame, variable Bildrate ohne PTS schlägt fehl, konstante Bildrate nutzt Half-up-Rundung. |
| `endoreg_db/services/video_files/_time.py` und `metadata.py` | **migriert** | Laden persistierte Nachbar-PTS und delegieren an `video_timeline.py`. `VideoFile.frame_number_to_s` und `s_to_frame_number` sind dünne Wrapper. |
| `models/label/label_video_segment/label_video_segment.py:start_time,end_time` | **migriert** | Segmentgrenzen verwenden den gemeinsamen Frame→PTS-Resolver. |
| `serializers/label_video_segment/label_video_segment.py` | **migriert** | Create, Update und Response verwenden den zentralen Timestamp↔Frame-Resolver. Fehler bei variabler Bildrate werden nicht durch Default-FPS verdeckt. |
| `services/video_segments_bulk_mutation.py` | **migriert** | Übergibt Create/Update an den PTS-fähigen Segmentserializer. Der Nutzen hängt davon ab, dass Consumer Timestamps mitsenden. |
| `serializers/video/video_file.py` | **überwiegend migriert** | Segmentzeiten verwenden Boundary-PTS. Der separate Duration-Fallback `total_frames / fps` ist nur Metadatenableitung und bleibt Priorität 2. |
| `services/segment_sync.py` | **migriert** | Erstellung und Änderungsvergleich verwenden persistierte PTS. |
| `services/video_storage/timelines.py` | **migriert** | Normalisierungsevidenz materialisiert Segmentgrenzen mit `pts_v1`; eine variable Bildrate ohne vollständige Boundary-PTS schlägt fehl. |
| `services/video_files/frames.py:extract_video_frame_range_by_timestamps` | **migriert** | Timestampbereiche werden zentral aufgelöst und mit Timestamp und Frameindex strukturiert protokolliert. |
| `utils/extract_specific_frames.py` und `serializers/Frames_NICE_and_PARIS_classifications.py` | **bedingt sicher** | Auswahl erfolgt über Quell-Decode-Indizes. Ein Legacy-`fps`-Argument wird ignoriert; diese Pfade erzeugen selbst keine klinischen Timestamps. |

Regressionsnachweise liegen insbesondere in
`tests/services/test_video_temporal_mapping.py`,
`tests/services/test_segment_frame_extraction.py`,
`tests/import_files/test_video_import_normalization.py` und
`tests/services/test_video_storage_normalization.py`.

## Priorität 1: lx-annotate – vollständige Klassifikation

| Callsite | Status | Bewertung oder Restarbeit |
| --- | --- | --- |
| `frontend/src/components/VideoExamination/VideoExaminationAnnotation.vue` | **bedingt sicher** | Browser-Playback und Seeking verwenden Sekunden (`currentTime`) und führen selbst keine Framekonvertierung durch. Segmentmutationen delegieren an den timestamp-first `videoStore`; offen bleibt, die geladene HLS-Generation nachweislich an dieselbe Backend-Timeline zu binden. |
| `frontend/src/components/VideoExamination/VideoExaminationAnnotation.vue:ensureSegmentationFpsReady` | **beabsichtigt** | `annotation_fps_resample_v1` erzeugt vor der ersten Segmentzeile bewusst eine neue 50-FPS-CFR-Timeline. Die UI blockiert bis `ready`; der noch fehlende Cross-Repository-Test muss atomare HLS-Veröffentlichung und Generationstreue nachweisen. |
| `frontend/src/stores/videoStore.ts:backendSegmentToSegment` | **migriert als Consumer** | Bevorzugt Backend-`start_time`/`end_time` und übernimmt kanonische Framegrenzen. Es darf nicht auf lokale FPS-Rekonstruktion zurückfallen. |
| `frontend/src/utils/timeHelpers.ts` und `timeUtils.ts` | **offen, latent** | Exportieren weiterhin generische `secondsToFrames`/`framesToSeconds`, einmal sogar mit Default 50. Produktionscode nutzt derzeit hauptsächlich Format-/Layoutfunktionen; Tests normalisieren die unsichere CFR-Annahme. Funktionen auf explizite CFR-Nutzung begrenzen oder entfernen. |
| `frontend/src/components/Anonymizer/OutsideSegmentComponent.vue` | **bedingt sicher** | Übergibt Backend-Segmentzeiten und nutzt `currentTime`; keine FPS-Konvertierung. Sicherheit setzt dieselbe veröffentlichte Generation für Segmentdaten und Video voraus. |
| `frontend/src/components/Anonymizer/AnonymizationValidationComponent.vue` | **bedingt sicher** | Synchronisiert Raw/Processed über Media-Timestamps. Das ist keine Frameidentitätsgarantie; Qualitätsprüfung muss PTS-basierte Vergleichsframes separat verwenden. |
| `frontend/src/views/reporting/FrameSelectorPage.vue` | **bedingt sicher** | Operiert auf Backend-Frameindizes und Frame-Stream-URLs. Der angezeigte Timestamp ist nur so korrekt wie `frame_stream.py`. |
| `lx_annotate/hub/hub_export_payloads.py` | **migriert als Transport** | Übernimmt `frame.timestamp` in den Exportvertrag und berechnet ihn nicht aus FPS. |

Generierte `VideoExaminationAnnotation.vue.js`-Dateien wurden nicht als eigene
Callsites gezählt. Maßgeblich sind die Vue-/TypeScript-Quellen.

## Priorität 2: Analysefenster und abgeleitete Metadaten

Diese FPS-Nutzungen verändern derzeit keine persistierte klinische
Frameidentität, können bei variabler Bildrate aber zeitlich ungleichmäßige Fenster oder
ungenaue abgeleitete Zeitangaben erzeugen:

- `endoreg_db/utils/ai/predict.py` und `utils/ai/postprocess.py`;
- `models/metadata/video_prediction_logic.py` und `video_prediction_meta.py`;
- `utils/calc_duration_seconds.py`;
- `serializers/video/video_file.py` im OpenCV-Duration-Fallback;
- `management/commands/profile_segment_updates.py`.

Die verbleibenden zeitbasierten Modellfenster sollen langfristig über PTS-Bereiche
definiert werden. Reine CFR-Performanceprofile dürfen FPS weiter verwenden, müssen CFR
aber als Vorbedingung prüfen und dürfen keine klinischen Boundary-IDs erzeugen.

`services/video_temporal_inference.py` ist migriert: Glättung, Mindestdauer und
Lückenschluss werden anhand persistierter Präsentationszeitstempel ausgewertet.
Fehlende oder inkonsistente Zeitstempelfolgen brechen die Inferenz laut ab;
Ergebnisgrenzen werden auf die ursprünglichen Frame-Nummern zurückgeführt.

## Beabsichtigte oder unkritische FPS-Nutzung

- Codec-/HLS-Ausgabegrenzen und `annotation_fps_resample_v1` erzeugen bewusst
  eine neue, versionierte Timeline.
- FPS-Probing und Metadatenanzeige lesen eine Rate, ohne Frameidentitäten
  abzuleiten.
- `select=...n...` ist für die konkrete, geprüfte Video-Generation eine
  Frameindexoperation. Nur der daraus berichtete Timestamp muss PTS-basiert
  sein.
- Timeline-Layout als `timestamp / duration` ist korrekt, wenn beide Werte zur
  gleichen veröffentlichten Generation gehören.
- Tests und Fixtures dürfen FPS zur Konstruktion expliziter CFR-Fälle nutzen.

## Verbindlicher Backend-/Frontend-Vertrag

Für Segment-Create und -Update gilt:

1. lx-annotate sendet `start_time` und `end_time` als Media-Timestamps in
   Sekunden, ohne lokale FPS-Quantisierung.
2. endoreg-db validiert die Timestamps gegen die aktuelle Video-Generation und
   löst sie über persistierte PTS beziehungsweise den expliziten CFR-Fallback
   auf.
3. Die Response enthält kanonische `start_time`, `end_time`,
   `start_frame_number`, `end_frame_number` und die zugehörige
   `timeline_version` beziehungsweise Generationsevidenz.
4. lx-annotate ersetzt optimistische Grenzen vollständig durch die Response.
5. Fehlende Präsentationszeitstempel bei variabler Bildrate, gemischte Generationen und laufende FPS-Normalisierung
   blockieren Mutationen laut.

Der bestehende Bulk-Mutation-Endpunkt reicht Timestampfelder an den
Segmentserializer weiter. `videoStore.ts:createSegment` und die Updatepfade
verwenden diesen Vertrag jetzt ohne clientseitige Frameableitung.

## Fehlende Regressionstests

1. **lx-annotate VideoStore bei variabler Bildrate – umgesetzt:**
   `frontend/src/utils/__tests__/segmentTimeline.test.ts` prüft irreguläre
   Timestamps, timestamp-only Create/Update und die Übernahme der kanonischen
   Backend-Antwort.
2. **lx-annotate Timeline bei variabler Bildrate – umgesetzt:** Frame vor/zurück
   nutzt den getesteten Backend-Nachbarschaftsvertrag und dessen kanonische PTS
   statt `1 / fps`.
3. **endoreg-db Frame-Stream:** `X-Frame-Timestamp` und `FrameSample.timestamp`
   entsprechen bei variabler Bildrate dem persistierten PTS.
4. **Cross-Repository >50 FPS:** Zustände
   `required→queued→running→ready/failed`, authentifiziertes Processed-HLS,
   atomare Generation, Lease-Konkurrenz und stabile Segment-PTS nach Reload.
5. **Negativfall Generation:** Segmentdaten einer alten Generation dürfen nicht
   gegen neu veröffentlichtes HLS editierbar werden.
6. **gc-10-Regression für nominelle 50 FPS:** Ein Fixture mit nominellem
   `r_frame_rate=50/1`, beobachtetem `avg_frame_rate=49.8549` und belegter
   `annotation_fps_resample_v1`-Generation prüft Processed-HLS bis zur atomaren
   Veröffentlichung. Positive Evidenz umfasst identische Frameanzahl,
   zulässige Dauerabweichung und stabile PTS-/Segmentgrenzen. Negativtests
   lehnen echte Timeline-Drift und fehlende Provenienz ab. Ein deterministischer
   Validierungsfehler wird nicht unbegrenzt erneut zugestellt und blockiert
   keine unabhängige nachfolgende HLS-Arbeit.

## Rückwärtskompatible Abschaltung von `transcode_fps`

1. API und `lx_dtypes` akzeptieren das Feld vorübergehend weiter.
2. Der Annotationsexporter protokolliert, dass der Legacywert ignoriert wird,
   und erzeugt identitätserhaltende Frames.
3. Aktuelles lx-annotate sendet das Feld nicht mehr.
4. Telemetrie identifiziert verbleibende Altclients.
5. Erst eine spätere versionierte API entfernt das Feld. Es erhält keine neue,
   mehrdeutige Bedeutung.
