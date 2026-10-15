# Review der Implementierungsrichtung vom 14. Oktober 2026

Die untersuchten Änderungen verbessern Fehlerbehandlung und Artefaktbesitz deutlich.
Eine vollständige Erfüllung aller Repository-Standards ist damit noch nicht belegt.
Dieses Dokument ist eine technische Review-Aufnahme, kein zweiter Freigabetracker.
Maßgeblich bleiben [VideoStorageNormalization.yml](../feature-tracking/VideoStorageNormalization.yml)
und [Reporting.yml](../feature-tracking/Reporting.yml).

## Umfang und Nachweisgrenzen

Geprüft wurden die vom Owner `codex-endoreg-durable-01a1398f` bestätigten Änderungen
an Report-Retry, Video-Rebuild, Generation-Cleanup und Provenance-Recovery sowie
die zugehörigen Tests und die Recovery-Komponente in lx-annotate. Der referenzierte
Chat-Thread war nicht direkt lesbar; Änderungen und Tracker-Nachrichten waren zugänglich.
Unabhängige Annotation- und Jumpmark-Änderungen im gemeinsamen Checkout wurden nicht
diesem Agenten zugerechnet. Parallel laufende Bearbeitung kann Zeilennummern verschieben.

## Befunde

### R1 — Hoch: Verschlüsselte Staging-Grenze bleibt unbewiesen

**Regel:** Lokale Medien einschließlich temporärer Rohdaten müssen innerhalb der
freigegebenen verschlüsselten Speichergrenze verbleiben.

**Beleg:** `endoreg_db/services/hub/video_provenance_recovery.py:88` und `:104`
materialisieren verarbeitetes beziehungsweise Rohvideo über `ensure_local_file`.
`endoreg_db/utils/storage/files.py:181` bis `:193` schreibt entschlüsselte Bytes
atomar mit Modus `0600` unter `get_runtime_paths().transcoding`. Dieser Pfad prüft
Containment und Dateirechte, aber keine tatsächlich verschlüsselte Ablage.

**Auswirkung:** Ohne schützende Storage-Schicht können Rohdaten auf dem Datenträger
im Klartext liegen. Normales Aufräumen schützt nicht gegen Prozessabbruch oder
physischen Datenträgerzugriff. Dies ist eine bestehende Infrastruktur-/Helper-Lücke,
die der neue Recovery-Pfad ebenfalls verwendet, keine neu eingeführte Kryptografie.

**Korrektur:** Freigegebene Speichergrenze im Deployment nachweisen und vor solchen
Operationen verbindlich prüfen. Vorhandene LuxNix-Unterstützung und zentrale
Storage-Helper verwenden. Keine eigene Verschlüsselung im Recovery-Service ergänzen.
Ein Test mit verschlüsseltem Quellblob und anschließend gelöschtem Tempfile beweist
die Verschlüsselung des temporären Datenträgers nicht. Bereits anderweitig attestierte
Hardware-/Dateisystemverschlüsselung könnte diese Deployment-Lücke schließen; deren
aktueller Nachweis ist nicht Bestandteil dieser Quellcodeprüfung.

### R2 — Mittel: Wiederholte Recovery ist nicht vollständig idempotent

**Regel:** Wiederholung derselben logischen Operation darf keinen neuen fachlichen
Zustandswechsel erzeugen.

**Beleg:** `video_provenance_recovery.py:130` bis `:143` speichert bei jedem
`dry_run=False` erneut `meta` und `date_modified` und erzeugt einen neuen
`AuditLedger`-Eintrag. Auch ein bereits identischer `previous`-Receipt wird nicht
als Wiederholung erkannt. Die Tests prüfen Preview, Apply und Ablehnungen, aber
keinen identischen zweiten Apply-Aufruf.

**Auswirkung:** Wiederholung nach verlorener HTTP-Antwort erzeugt zusätzliche
Bestätigungen und erneute teure Medienprüfung. Die Medienbytes werden dadurch
nicht automatisch verändert; der Befund betrifft fachliche Mutation und Audit.

**Korrektur:** Gleichheit beziehungsweise einen stabilen Operationsschlüssel unter
derselben Sperre prüfen und den vorhandenen Receipt zurückgeben. Falls jeder
HTTP-Versuch auditiert werden soll, diesen Versuch vom einmaligen fachlichen
Anerkennungsereignis unterscheiden. Regressionstest für zwei identische Aufrufe,
abweichende Zeitangaben und konkurrierende Wiederholungen ergänzen.

### R3 — Mittel: API-Vertrag mehrfach definiert, Zeitprüfung zu spät

**Regel:** Externe Eingaben einmal an der Grenze validieren; gemeinsame Typen und
generierte API-Verträge als maßgebliche Quelle verwenden.

**Beleg:** `endoreg_db/views/video/provenance_recovery.py:19` definiert ein lokales
Request-Modell, `:27` separat einen DRF-Serializer. Beide akzeptieren Zeitangaben
zunächst als Strings. Zeitzone und Reihenfolge prüft erst der gemeinsame
`VideoProvenanceRecovery`-Konstruktor nach Hashing, Entschlüsselung und Timeline-Probe.
Die lx-annotate-Komponente `VideoProvenanceRepairPanel.vue:59` definiert daneben
`RecoveryResponse` von Hand mit `mappingStatus: string` und
`electricalMatchingEligible: boolean`, obwohl der gemeinsame Vertrag engere
Literal-Werte besitzt. Die Laufzeitprüfung einzelner Antwortfelder ist positiv,
ersetzt aber keine gemeinsame Typquelle.

**Auswirkung:** Ungültige Zeitangaben belegen unnötig die Medienoperation; manuelle
Vertragskopien können auseinanderlaufen. Der abschließende Pydantic-Validator
verhindert ungültige Persistenz, daher ist dies kein belegter Integritäts-Bypass.

**Korrektur:** Eingabevalidierung am Request-Rand aus einem gemeinsamen Vertrag
ableiten, DRF/OpenAPI daran anbinden und den Frontendtyp generieren. Keine weitere
parallele DTO-Hierarchie einführen. Ungültige Zeitangaben müssen ohne Medien-I/O
abgelehnt werden; ein Contract-Test muss Request, Schema und Consumer verbinden.

## Positiv geprüfte Richtung

- Feature-YAML bleibt maßgeblich; der experimentelle Release-Nachweis bezeichnet
  sich ausdrücklich nicht als gewählten Deployment-Weg.
- Gemeinsame lx-dtypes-Receipts, Hash-Helper, Runtime-Pfade und vorhandene atomare
  Dateifunktionen werden genutzt. Neue Ablaufsteuerung liegt in Services.
- Entfernte pauschale Frame-Rollbacks respektieren den Artefaktbesitz: Ein
  Rebuild-Job besitzt nicht automatisch bereits vorhandene Frame-Caches.
- Report-Retry ruft nicht mehr den endgültigen Fehlerabschluss samt Löschung auf.
- Receipt-Reconciliation verlangt authentifizierten aktuellen Ursprung sowie eine
  fehlende, nicht referenzierte Ersetzung; sie löscht selbst keine Videodateien.
- Authentifizierung, Zentrumsscope, Medien-Leases, Hashprüfung und klinische
  Freigaben bleiben erhalten. Ungeklärte Provenance wird nicht als bewiesene
  Frame-Zuordnung dargestellt; elektrische Zuordnung bleibt gesperrt.
- Im geprüften Änderungsumfang wurden weder Master-Key-Übertragung noch ein
  Shared-Secret-Ersatz für Payload-Verschlüsselung oder mTLS-Bypass gefunden.
  Dies ist keine vollständige Prüfung aller Transportpfade.

## Verifikation

Eigener vollständiger Aufruf `.devenv/state/venv/bin/pyright`: **0 Fehler,
19 Warnungen**; überwiegend fehlende Typ-Stubs und ungenutzte Symbole.
Die Aussage gilt für den gemeinsamen Checkout zum Prüfzeitpunkt.

Vom Implementierungsowner gemeldet, hier nicht erneut ausgeführt: Recovery
13 Tests bestanden; Report-Lane 84 bestanden/6 bedingte Skips; Frontend 47
bestanden plus TypeScript/ESLint. Cleanup- und Rebuild-Ergänzungen waren noch in
Bearbeitung. Es lief bereits ein fokussierter pytest-Prozess; kein zusätzlicher
paralleler Datenbank-Testlauf wurde gestartet. Laufende Hostdienste sind kein
Beweis, dass die neuesten Quellcodeänderungen ausgerollt sind.

Die Befunde wurden dem Implementierungsowner über den Feature-Tracker zugestellt.
Er hat R1–R3 angenommen und Folgeänderungen für Wiederholungsbehandlung sowie
gemeinsame Zeit-/Antworttypen angekündigt. Diese Folgeänderungen sind nicht Teil
des hier geprüften Snapshots und noch nicht unabhängig nachgeprüft.
Strukturprüfung: 65 Manifeste gültig. Tracker-Validierung: 37 Feature-Definitionen
gültig. Diese Prüfungen bestätigen Schema und Referenzen, keine klinische Freigabe.
Dieses Review ändert weder Anwendungscode noch Produktionsdaten oder Freigabestatus.
