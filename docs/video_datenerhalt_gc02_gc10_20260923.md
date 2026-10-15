# Datenerhalt und Videozustände auf gc-02 und gc-10

Historisches Betriebsprotokoll vom 2026-09-23; keine Aussage zum heutigen Dienstzustand. Aktuelle Freigaben und Bereitschaftsnachweise stehen ausschließlich im Feature-Tracker.

| Field | Value |
| --- | --- |
| `datum` | 2026-09-23 |
| `sprache` | de |
| `zweck` | Betriebsprotokoll der ausdrücklich beauftragten Bestandserhaltung. Verbindlicher Funktions- und Freigabestatus steht ausschließlich im Feature-Tracker. |


## Referenzen

| Field | Value |
| --- | --- |
| `feature` | feature-tracking/ImportPipelineRobustness.yml |
| `kriterium` | import_reconciliation_and_recovery |
| `speicherregeln` | docs/video_storage_normalization.md |
| `speicherfreigaben` | feature-tracking/VideoStorageNormalization.yml |

## Umfang

| Field | Value |
| --- | --- |
| `gc_02` | 7 |
| `gc_10` | 31 |
| `profil_beider_hosts` | site-client |
| `zugriff` | gc-02 über lokales Terminal; gc-10 über SSH, jeweils bestehende Dienstumgebung. |
| `eingriff` | Bestehende Dateien und klinische Arbeit erhalten, fehlende beweisbare Prüfsummen ergänzen, passende Streaming-Zuordnungen wiederherstellen, abgelaufene Bearbeitungsversuche eindeutig beenden. |


## Ergebnis

| Field | Value |
| --- | --- |
| `videos_mit_mindestens_einer_vorhandenen_mediendatei` | 38 |
| `datensaetze_mit_transaktionalen_vorher_nachher_belegen` | 37 |
| `aktiver_import_beim_eingriff_unveraendert` | gc-10 Video 58 |
| `vollstaendig_gepruefte_und_ergaenzte_processed_pruefsummen` | 25 |
| `wiederhergestellte_streaming_zuordnungen` | 21 |
| `abgelaufene_streaming_versuche_beendet` | 3 |
| `abgelaufene_importversuche_beendet` | 2 |
| `unvollstaendige_videos_mit_ehrlichem_fehlerstatus` | 5 |
| `erhaltene_annotationen_gc_02` | 376607 |
| `erhaltene_annotationen_gc_10_gepruefte_datensaetze` | 368498 |
| `erhaltene_segmente_gc_02` | 264 |
| `erhaltene_segmente_gc_10_gepruefte_datensaetze` | 3137 |

### Aktuelle verarbeitete streams mit datei und schluesselpruefung

| Field | Value |
| --- | --- |
| `gc_02` | 3 |
| `gc_10` | 22 |
| `keine_irreversiblen_medienverluste_nachgewiesen` | true |

## Rohdateien nach validierung

**einordnung:** Eine fehlende Rohdatei allein ist kein Datenverlust. Laut Betreiber werden Rohdateien bei der Validierung über VideoExaminationAnnotation.vue gezielt bereinigt. Der Backend-Pfad bestätigt die vorgesehene Löschung nach Validierung bei erfüllten Bereinigungsbedingungen; der anonymisierte Master bleibt erhalten. Für jeden historischen Löschvorgang wurde kein eigener Lognachweis rekonstruiert.


### Betroffene videos ohne rohdatei

#### Gc 02

- 2

#### Gc 10

- 12
- 34
- 36
- 54

**korrektur_video_12_gc_10:** Die während dieser Bearbeitung zunächst gesetzte missing_raw_warning wurde zurückgenommen. Ausschließlich die selbst gesetzten Integritätsfelder wurden auf ihren vorherigen Wert zurückgesetzt; sonstige Metadaten blieben erhalten. Die anonymisierte Datei, 29698 Annotationen und 13 Segmente sind vorhanden. Der historische fehlgeschlagene Rohstream bleibt als Historie bestehen.


### Code

- ../dev/lx-annotate/frontend/src/components/VideoExamination/VideoExaminationAnnotation.vue
- endoreg_db/services/video_files/validation.py::validate_video_metadata_annotation
- endoreg_db/services/video_files/io.py::delete_raw_file_after_validation

## Befunde zu fehlenden daten und ursachen

### 1

**punkt:** Fehlende Prüfsummen vorhandener anonymisierter Dateien

#### Gc 02

- 4
- 6

#### Gc 10

- 13
- 14
- 15
- 19
- 21
- 23
- 25
- 26
- 27
- 34
- 36
- 38
- 39
- 40
- 41
- 44
- 45
- 46
- 47
- 48
- 50
- 51
- 52

**nachweis:** Dateien waren vorhanden und wurden vollständig entschlüsselt gehasht. Gespeicherter processed_video_hash war leer. Die ursprüngliche Ursache fehlender Persistenz ist nicht belegt; ein Verlust der Videobytes folgt daraus nicht.


**auswirkung:** Die Übersicht konnte vorhandene fertige Streams wegen der abweichenden abgeleiteten Generationsidentität nicht als aktuelle Generation erkennen.


**massnahme:** Prüfsummen über get_file_hash ergänzt. Bei 21 Streams stimmten tatsächlicher Quellinhalt, Quellpfad und vorhandene Herkunftsangaben überein; nur die Generationszuordnung wurde korrigiert. Keine Dateien neu kodiert.


### 2

**punkt:** Fehlende sensible Metadaten

#### Gc 02

- 3
- 15

#### Gc 10

- 35
- 37
- 42
- 59

**einordnung:** Fehlende Verknüpfung festgestellt. Ob Daten zuvor bestanden oder die Extraktion nie fertig wurde, ist nicht für jeden Fall belegt. Keine Metadaten erfunden und keine bestehenden Annotationen oder klinischen Freigaben zurückgesetzt. Bei gc-02 Video 3 bleibt eine nachvollziehbare Integritätswarnung.


### 3

**punkt:** Fehlende anonymisierte Ausgabe

#### Gc 02

- 15

#### Gc 10

- 35
- 37
- 42
- 58
- 59

**einordnung:** Rohdateien sind vorhanden. Kein Beleg, dass eine früher erfolgreich erzeugte anonymisierte Ausgabe gelöscht wurde. Video 58 hatte beim Eingriff einen aktiven Import und blieb unverändert. Die übrigen fünf Videos wurden als unvollständige Verarbeitung mit erhaltener Quelle gekennzeichnet.


### 4

**punkt:** Verwaiste laufende Versuche

#### Gc 02

##### Streaming artefakte

- 60
- 76
- 69

**import_video:** 15

#### Gc 10

| Field | Value |
| --- | --- |
| `import_video` | 59 |
| `nachweis` | Abgelaufene Bearbeitungsreservierungen beziehungsweise veraltete Materialisierungsversuche. Der gc-02 Import war rund acht Tage alt. Die Startbedingung des Streaming-Nachlaufs berücksichtigt offene Statuswerte, ohne abgelaufene Importreservierungen auszunehmen; dadurch konnte sie blockieren. |


**massnahme:** Importbesitz über den vorhandenen Reservierungsdienst neu abgegrenzt und Fehlerabschluss durchgeführt. Veraltete Streaming-Versuche unter Zeilensperren beendet, ohne Schlüssel, Pfade, Segmente oder Quelldateien zu bereinigen.


### 5

**punkt:** Fehlende historische Streaming-Herkunft

#### Gc 02

##### Verarbeitete videos

- 4
- 6

##### Roh videos

- 3
- 4
- 5
- 6
- 13

**einordnung:** Alte Artefakte ohne belegten Quellinhaltshash. Keine sichere automatische Übernahme allein anhand von Dateinamen oder Dateiexistenz möglich. Dateien und Schlüssel bleiben erhalten; kein nachgewiesener Masterverlust.


### 6

**punkt:** Streaming gehört zu einer anderen Generation

#### Gc 02

- 5

#### Gc 10

- 53
- 54

**einordnung:** Vorhandene ältere Streams stimmen nicht mit der aktuellen verarbeiteten Quelle überein. Keine unbelegte Neuzuordnung oder Löschung durchgeführt. Aktuelle Master und Annotationen bleiben erhalten.


### 7

**punkt:** Neuerzeugung auf gc-10 scheitert an der Grafiktreiberumgebung

#### Besonders sichtbar bei verarbeiteten streams

- 44
- 50

**nachweis:** Geladenes NVIDIA-Kernelmodul 580.119.02 und NVIDIA Management Library (NVML) 595.45 passen nicht zusammen. Worker melden unsupported device. Dies belegt einen aktuellen Verarbeitungsblocker, nicht die Ursache jedes historischen Importfehlers. Auf gc-02 waren die Versionen 595.45.04 konsistent.


### 8

| Field | Value |
| --- | --- |
| `punkt` | Geringer freier Speicher |
| `gc_02` | Ungefähr 94 Prozent belegt, 32 GiB frei zum Diagnosezeitpunkt. |
| `gc_10` | Ungefähr 95 Prozent belegt, 51 GiB frei zum Diagnosezeitpunkt. |
| `einordnung` | Verbleibendes Betriebsrisiko für neue Verarbeitung. GiB bezeichnet Gibibyte. Keine Mediendateien zur Platzgewinnung gelöscht; vorhandene verschlüsselte Speicherräume weiterverwendet. |


### 9

| Field | Value |
| --- | --- |
| `punkt` | Generischer Fehlerpfad kann Streaming-Schlüssel und Pfade entfernen |
| `code` | endoreg_db/services/streaming/hls_media.py::_mark_artifact_failed |
| `einordnung` | Im Code beobachtetes Bereinigungsverhalten; kein Beweis, dass dieser Pfad einen konkreten historischen Verlust verursacht hat. Bei dieser Maßnahme wurde er für die drei veralteten Streaming-Versuche ausdrücklich nicht benutzt. |


## Schutz und pruefungen

- Vor jeder Änderung verschlüsselte, zurückgelesene Belege im jeweiligen lokalen Medienspeicher.
- Geplante Änderungen zunächst innerhalb von Transaktionen ausgeführt und zurückgerollt.
- Änderungen unter Datenbank-Zeilensperren und Prüfung aktiver Medienreservierungen.
- 25 vollständige Klartext-Prüfsummenprüfungen über den vorhandenen Hash-Helfer.
- Vorhandene kanonische Dateien zusätzlich mit authentifizierten Anfangs- und Endbereichslesungen geprüft.
- Annotationen und Segmente über vollständige Zeilendaten-Prüfsummen vor/nach dem Eingriff verglichen.
- Bildanzahl, Bildrate, Dauer, Zeitkoordinaten und klinische Freigabefelder unverändert.
- Masterreferenzen, Dateisystemidentität, Dateigröße und Änderungszeit vor/nach dem Eingriff unverändert.
- Streaming-Schlüssel, Initialisierungsvektoren, Pfade, Segmentanzahl und Quellinhaltshashes unverändert.
- Abschlussprüfung: Alle von der Übersicht ausgewählten fertigen Streams haben vorhandene Dateien und entschlüsselbare Schlüssel.
- Kein Export von Rohmedien; keine Patientendaten, Zugangsdaten oder Schlüssel in diesem Dokument.

Prüfumfang und offene Nachweise des historischen Eingriffs stehen in
[ImportPipelineRobustness.yml](../feature-tracking/ImportPipelineRobustness.yml),
Kriterium `import_reconciliation_and_recovery`.

## Abgeleitete backend aenderungen

**auftrag:** Nachprüfung der Reparaturbefunde auf notwendige Änderungen in endoreg_db.

### Rohdatei reconciliation

| Field | Value |
| --- | --- |
| `datei` | endoreg_db/services/runtime/reconciliation.py |
| `befund` | Die Start-Reconciliation konnte bei einer noch gesetzten Rohdateireferenz nach klinischer Validierung eine alte Eingangskopie erneut zuordnen. |


**aenderung:** Validierte Videos werden vor der Kandidatensuche ausgenommen. Vor einer Dateipromotion wird das Video unter Datenbank-Zeilensperre frisch geladen. Zwischenzeitliche Validierung, geänderte Quellidentität oder Referenz und eine inzwischen wieder vorhandene Rohdatei verhindern die Änderung.


### Uebersicht

| Field | Value |
| --- | --- |
| `datei` | endoreg_db/serializers/misc/file_overview.py |
| `befund` | Historische fehlgeschlagene Rohstreams konnten auch nach erfolgreicher klinischer Validierung als aktueller Fehler angezeigt werden. |


**aenderung:** Ausschließlich der historische fehlgeschlagene Rohstream entfällt aus der aktuellen Übersicht, wenn klinische Validierung vorliegt und der vorhandene fertige anonymisierte Stream zur aktuellen Quellmetadaten-Identität passt. Aktive Rohverarbeitung sowie fehlende, veraltete oder fehlerhafte anonymisierte Streams bleiben sichtbar. Die gespeicherte Historie bleibt erhalten.


### Weitere gepruefte befunde

- Die aktuellen Import- und Anonymisierungspfade speichern processed_video_hash bereits vor ensure_video_hls. Keine belegte neue Ursache der historischen Prüfsummenlücken in diesen Pfaden festgestellt; keine spekulative automatische Übernahme alter Streaming-Generationen ergänzt.

- Die allgemeine Video-Integritätsprüfung prüft den kanonischen anonymisierten Master und setzt fehlende Rohdateien allein nicht auf verloren.

- Veraltete Importzustände werden von der Start-Reconciliation bereits bewusst beibehalten, weil die Zuordnung zum besitzenden Verarbeitungsversuch fehlt. Kein pauschales Zurücksetzen ergänzt, das aktive Verarbeitung gefährden würde.

- NVIDIA-Treiberkonflikt und Startbedingung des Streaming-Nachlaufs liegen in der Host-/Dienstkonfiguration; sie werden durch diese Backend-Änderung nicht behoben.


**auslieferung:** Quellcodeänderung im Arbeitsverzeichnis; noch nicht auf gc-02/gc-10 ausgerollt.

### Code pruefungen

- Fokussierter Pyright für die vier geänderten Python-Dateien: 0 Fehler, 0 Warnungen. Ruff check und format --check bestanden.

- Gemeinsamer pytest-Lauf für test_reconciliation_service.py, test_anonymization_overview.py und test_media_integrity_reconciliation_plan.py: 76 bestanden; drei neue Fälle zunächst wegen fehlendem Pflichtfeld center im Testaufbau fehlgeschlagen. Nach Korrektur und erneuter Typprüfung bestanden diese drei Fälle im fokussierten Wiederholungslauf. Insgesamt 79 unterschiedliche Fälle erfolgreich geprüft; bestehende Plugin-/Abhängigkeitswarnungen.

- Die Datenbankfälle simulieren eine Änderung während der Kandidatensuche. SQLite prüft dabei den erneuten Zustandsvergleich, nicht die Wirksamkeit konkurrierender PostgreSQL-Zeilensperren.

- Vollständiger Projekt-Pyright meldete zunächst sechs Fehler und sechs Warnungen. Fünf Fehler in neuen Testfunktionen wurden korrigiert und fokussiert nachgeprüft. Der verbleibende, außerhalb dieses Umfangs beobachtete Fehler betraf serializers/report/patient_examination_report.py:399 (attached_findings, teilweise unbekannter Listentyp). Keine fehlerfreie Gesamtprüfung behauptet.


## Belege

| Field | Value |
| --- | --- |
| `basis` | Je Host get_runtime_paths().storage; Speicherung verschlüsselt über den vorhandenen Django-Medienspeicher. |
| `gc_02` | recovery/video-preservation-20260923-89225c4a1ea342d68d2938f1b7f03134 |
| `gc_10` | recovery/video-preservation-20260923-60aea4d60b024df495230b8b88c4f073 |

### Dateien

- before.json
- verified-processed-identities.json
- rollback-test-summary.json
- rollback-test-video-<id>-before.json / -after.json
- executed-preservation-command.py
- committed-video-<id>-before.json / -after.json
- committed-summary.json
- final-verification.json
- gc-10 zusätzlich: raw-cleanup-correction-before.json / raw-cleanup-correction-after.json

**hinweis:** Die ursprünglichen unveränderten Ausführungsbelege enthalten noch die zunächst gesetzte Rohdatei-Warnung für Video 12. Der gesonderte Korrekturbeleg und die Abschlussprüfung zeigen ihre Rücknahme. Belege bleiben lokal verschlüsselt.


## Einzelnachweis

### Gc-02

| video | rohdatei | anonymisierte_datei | annotationen | segmente | massnahmen |
| --- | --- | --- | --- | --- | --- |
| 2 | false | true | 35786 | 36 | Bestand unverändert erhalten |
| 3 | true | true | 67115 | 40 | Veralteter Streaming-Versuch beendet, Artefakt 60; Warnung wegen fehlender sensibler Metadaten |
| 4 | true | true | 90039 | 69 | Dateiprüfsumme ergänzt |
| 5 | true | true | 175670 | 83 | Veralteter Streaming-Versuch beendet, Artefakt 76 |
| 6 | true | true | 3428 | 1 | Dateiprüfsumme ergänzt; Veralteter Streaming-Versuch beendet, Artefakt 69 |
| 13 | true | true | 4569 | 35 | Bestand unverändert erhalten |
| 15 | true | false | 0 | 0 | Abgelaufener Importversuch abgesichert und beendet; Verarbeitung unvollständig; Quelle erhalten |

### Gc-10

| video | rohdatei | anonymisierte_datei | annotationen | segmente | massnahmen |
| --- | --- | --- | --- | --- | --- |
| 12 | false | true | 29698 | 13 | Rohdatei-Warnung zurückgenommen; erwartete Bereinigung |
| 13 | true | true | 7133 | 14 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 2 |
| 14 | true | true | 18804 | 19 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 3 |
| 15 | true | true | 13142 | 40 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 4 |
| 19 | true | true | 9566 | 23 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 5 |
| 21 | true | true | 21351 | 61 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 6 |
| 23 | true | true | 4197 | 15 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 8 |
| 25 | true | true | 1489 | 5 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 9 |
| 26 | true | true | 61817 | 43 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 10 |
| 27 | true | true | 26715 | 51 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 11 |
| 34 | false | true | 10321 | 51 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 12 |
| 35 | true | false | 0 | 0 | Verarbeitung unvollständig; Quelle erhalten |
| 36 | false | true | 48856 | 38 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 13 |
| 37 | true | false | 0 | 0 | Verarbeitung unvollständig; Quelle erhalten |
| 38 | true | true | 0 | 21 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 80 |
| 39 | true | true | 0 | 0 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 83 |
| 40 | true | true | 0 | 0 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 58 |
| 41 | true | true | 0 | 0 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 59 |
| 42 | true | false | 0 | 0 | Verarbeitung unvollständig; Quelle erhalten |
| 44 | true | true | 0 | 0 | Dateiprüfsumme ergänzt |
| 45 | true | true | 0 | 0 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 68 |
| 46 | true | true | 0 | 0 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 69 |
| 47 | true | true | 0 | 0 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 70 |
| 48 | true | true | 0 | 0 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 71 |
| 50 | true | true | 0 | 0 | Dateiprüfsumme ergänzt |
| 51 | true | true | 0 | 470 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 73 |
| 52 | true | true | 0 | 504 | Dateiprüfsumme ergänzt; Streaming-Zuordnung korrigiert, Artefakt 74 |
| 53 | true | true | 83564 | 1186 | Bestand unverändert erhalten |
| 54 | false | true | 31845 | 583 | Bestand unverändert erhalten |
| 58 | true | false | Aktiver Import; keine Änderung | Aktiver Import; keine Änderung | Aktiven Import unverändert belassen |
| 59 | true | false | 0 | 0 | Abgelaufener Importversuch abgesichert und beendet; Vorhandene technische Videometadaten im Status bestätigt; Verarbeitung unvollständig; Quelle erhalten |
