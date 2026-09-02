# Audit-Ledger: Vertrag und Betrieb

Dieses Dokument beschreibt den verbindlichen Anwendungs- und Betriebsvertrag
des persistierten `AuditLedger` in `endoreg_db`. Der Umsetzungs- und
Freigabestatus wird ausschließlich in
[`feature-tracking/AuditLedger.yml`](../feature-tracking/AuditLedger.yml)
geführt.

## Zweck und Vertrauensgrenze

Das Ledger weist nach, dass ausgewählte sicherheits- und freigaberelevante
Aktionen in einer bestimmten Reihenfolge persistiert wurden und anschließend
nicht unbemerkt verändert wurden. Jeder Eintrag enthält den Hash seines
Vorgängers. Der Singleton `LedgerHead` wird beim Anhängen innerhalb derselben
Datenbanktransaktion gesperrt und auf den neuen Eintrag gesetzt.

Das Ledger ist manipulationserkennend, aber keine digitale Signatur und kein
externer Zeitstempeldienst. Wer sowohl Ledgerzeilen als auch den Ledger-Kopf und
alle Backups kontrolliert, liegt außerhalb des nachgewiesenen Vertrauensmodells.
Die Datenbank, ihre Berechtigungen, Backups und der verschlüsselte
Speicherbereich müssen deshalb unabhängig geschützt werden.

Der Integritätsstatus ist global für die lokale Ledgerkette. Er gibt keine
Ledgerzeilen aus und ist keine center-gefilterte Ereignissuche. Der
produktionsseitig authentifizierte Statusendpunkt
`GET /api/audit-ledger/integrity/` liefert ausschließlich das Ergebnis der
letzten Hintergrundprüfung. Eine vollständige Kettenprüfung im Webprozess ist
ausgeschlossen.

Strukturierte Hub-Ereignisse unter dem Logger `endoreg_db.hub.audit` sind ein
separater betrieblicher Logkanal. Sie werden nicht automatisch zu
`AuditLedger`-Zeilen und besitzen nicht dessen Hashkettennachweis.

## Persistiertes Ereignisformat

Eine Ledgerzeile besteht aus:

- unveränderlicher UUID und serverseitigem Zeitstempel;
- optionalem authentifiziertem Django-Benutzer;
- `object_type`, `object_pk` und stabilem `action`-Code;
- einem kanonischen JSON-Objekt `data`;
- `prev_hash` und `hash` als SHA-256-Hexwerte.

Die Hash-Nutzlast wird durch
`lx_dtypes.models.contracts.audit_ledger.AuditLedgerHashPayload` definiert.
`data` akzeptiert nur endliche, JSON-kompatible Werte mit Stringschlüsseln.
Bestehende Zeilen dürfen über den Modellpfad nicht aktualisiert werden.

## Auditpflichtige Aktionen

Die derzeit bewusst persistierten Produzenten sind:

| Aktion | Fachlicher Anlass | Erforderliche Evidenz | Fehlerverhalten |
| --- | --- | --- | --- |
| `identity_committed` | Bindung validierter Medienmetadaten an pseudonyme Fallidentitäten | Medienart und -ID, SensitiveMeta-ID, Hashidentitäten, pseudonyme und verknüpfte IDs, Resolution-Ergebnis und Nutzlast-Hash | Die Identitätsauflösung bleibt transaktional; die generische Append-Hilfe kann bei noch nicht migrierter Ledger-Tabelle keinen Eintrag erzeugen und protokolliert dies |
| `ready_for_export` | Freigabe eines validierten, geschwärzten Processed-Videoartefakts | Center-Key, persistierter relativer Artefaktname, SHA-256 und Freigabestatus; kein absoluter Pfad | Ein nicht nachweisbar persistierter Ledger-Eintrag bricht die Exportfreigabe mit HTTP 503 ab und rollt den Zustandswechsel zurück |
| `center_admin_bootstrapped` | Promotion eines bereits Keycloak-synchronisierten Mitglieds von `center_scope:admin` | Zielbenutzer, erforderliche Gruppe, vorherige und neue Rollen sowie Initiator | Ein fehlender Ledgernachweis bricht die Promotion ab und rollt die Transaktion zurück |
| `media_storage_migrated` / `media_storage_failed` | Idempotente Medien-Storage-Migration oder deren klassifizierter Fehler | Objektbezug und Migrationsnachweis des Management-Befehls | Nicht verfügbare Ledger-Tabellen werden als Warnung sichtbar; der Migrationsbefehl verwaltet seine eigene Fehlerentscheidung |

Neue auditpflichtige Aktionen benötigen vor ihrer Einführung einen stabilen
Action-Code, einen benannten Produzenten, eine minimale typisierte Nutzlast,
eine klare Transaktionsgrenze und negative Tests für den Ausfall des Ledgers.
Ein allgemeiner Request-, ORM- oder Debug-Mitschnitt gehört nicht in das
Ledger.

## Datenminimierung und bekannte Grenze

Bewusst ausgeschlossen sind Rohmedien, Dateiinhalt, Large-Language-Model-
Prompts, vollständige Request-Payloads, Klartext-Secrets, der Master-Key,
Passwörter, Tokens, Private Keys, Stacktraces und direkte, für den Nachweis
nicht erforderliche Patientenidentifikatoren. Pseudonyme IDs und Hashwerte
dürfen nur aufgenommen werden, wenn sie für die konkrete Korrelation notwendig
sind. Das Ledger darf niemals als Ersatz für klinische Primärdaten verwendet
werden.

Das `ready_for_export`-Ereignis persistiert nur den relativen verwalteten
Storage-Namen und den Inhalts-Hash, nicht den absoluten aufgelösten Pfad. Neue
Ereignisse dürfen ebenfalls keine absoluten Pfade aufnehmen. Der globale
Integritätsendpunkt gibt weder diese Nutzlast noch einzelne Ledgerzeilen aus,
ist im Produktionsmodus authentifizierungspflichtig und stellt ausschließlich
eine `GET`-Operation bereit.

## Integritätszustände und Prüffrequenz

Die Hintergrundprüfung unterscheidet folgende Zustände:

| Status | Bedeutung | Betriebsentscheidung |
| --- | --- | --- |
| `verified` | Jede Zeile, jeder Vorgängerlink und der Ledger-Kopf stimmen überein | Regulärer Betrieb darf fortfahren |
| `failed` | Die Kette oder der Kopf stimmt nicht mit den persistierten Hashwerten überein | Fail-closed Incident; keine Reparatur oder Überschreibung |
| `error` | Die Prüfung konnte wegen eines Laufzeit- oder Infrastrukturfehlers nicht abgeschlossen werden | Ursache beheben und erneut prüfen; bis dahin nicht als verifiziert behandeln |
| `unknown` | Es liegt kein gültiges gecachtes Prüfergebnis vor | Nicht als verifiziert behandeln; Hintergrund- oder Operatorprüfung ausführen |

Standardmäßig plant Celery Beat alle 300 Sekunden den Task
`endoreg_db.refresh_audit_ledger_integrity_status` auf der separaten
Maintenance-Queue. Der Wert ist über
`CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS` konfigurierbar, muss aber
mindestens 60 Sekunden betragen. Die Planung ist standardmäßig aktiv und kann
über `CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_ENABLED` deaktiviert werden; ein
Produktionsprofil benötigt dann einen gleichwertigen dokumentierten externen
Aufruf.

Ein Cache-Lock mit 30 Minuten Laufzeit verhindert parallele vollständige
Prüfungen. Wird der Lock bereits gehalten, bleibt der letzte Status sichtbar
und erhält die Quelle `skipped_locked`. Fehlender oder nicht als Objekt
lesbarer Cacheinhalt führt fail-closed zu `unknown` und `verified=false`.

## Bedienung und Alarmierung

Eine explizite, lock-bewusste Prüfung wird im installierten Runtime-Environment
ausgeführt:

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py \
  refresh_audit_ledger_integrity --once --fail-on-non-verified
```

Maschinenlesbare Ausgabe ist JSON. `--pretty` formatiert sie für die manuelle
Diagnose. `--fail-on-non-verified` liefert einen Fehlercode für jeden anderen
Status als `verified` und ist für Deployment- und Recovery-Gates erforderlich.

Anschließend prüft der allgemeine Health-Check den gecachten Zustand:

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py \
  check_system_health --json
```

Im `local_study_server`-Profil ist ein nicht verifiziertes Ledger ein
fehlgeschlagener Health-Check. `audit_ledger.integrity_failed` und
`audit_ledger.integrity_error` werden als strukturierte Fehlerereignisse mit
Kopf-Hash, letzter Eintrags-ID und – soweit verfügbar – Eintragsanzahl
ausgegeben. Die Fehlerausgabe darf keine Ledger-Nutzlast oder Patientendaten
enthalten.

## Incident-Verfahren

Bei `failed`, `error` oder einem unerwartet alten `unknown` gilt:

1. Schreibende Freigabe-, Admin- und Migrationsworkflows stoppen; den Zustand
   nicht manuell auf `verified` setzen.
2. Datenbank-Snapshot, Ledgertabellen, `LedgerHead`, relevante strukturierte
   Logs, Task-ID, Hostzeit und Deploymentversion beweissichernd erfassen.
3. Den lock-bewussten Management-Befehl genau einmal erneut ausführen und JSON,
   Exit-Code sowie Ledger-Kopf vergleichen. Keine parallelen Vollscans starten.
4. Bei `error` zuerst Cache-, Datenbank-, Migrations- oder Workerursache beheben.
   Bei `failed` von Manipulation oder Korruption ausgehen und Security sowie
   Betrieb einbeziehen.
5. Datenbank und Ledger nicht automatisch reparieren, neu hashen, kürzen oder
   aus einer teilweise passenden Quelle zusammenführen. Rohzeilen und
   vorherige Backups unverändert erhalten.
6. Wiederherstellung nur aus einem zusammengehörigen, verifizierten
   Datenbank-Backup durchführen. Danach die vollständige Kette prüfen und erst
   nach dokumentierter Freigabe schreibende Workflows wieder öffnen.

## Aufbewahrung und Änderungskontrolle

Es gibt derzeit keinen automatischen Retention- oder Löschpfad für
`AuditLedger`-Zeilen. Sie werden zusammen mit der Datenbank gesichert. Eine
spätere Aufbewahrungs- oder Archivierungsregel benötigt ein versioniertes
Ketten-/Checkpoint-Konzept, Security- und Betriebsfreigabe sowie eine
Wiederherstellungsprobe; gewöhnliche Datenbereinigung darf keine Ledgerzeilen
löschen.

Änderungen am Hashformat, an Action-Codes oder an bestehenden Nutzlastfeldern
sind Persistenzvertragsänderungen. Sie benötigen eine lx-dtypes-
Kompatibilitätsstrategie, Migrationstests, Vorwärts-/Rollback-Nachweis und eine
Aktualisierung dieses Dokuments sowie der Feature-Evidenz.

## Verifikation

Aus `/home/admin/endoreg-db`:

```sh
.devenv/state/venv/bin/pytest \
  tests/models/state/test_audit_ledger.py \
  tests/services/test_audit_integrity.py \
  tests/services/test_hub_audit.py \
  tests/management/commands/test_refresh_audit_ledger_integrity.py \
  tests/views/misc/test_stats_endpoints.py \
  tests/management/commands/test_check_system_health.py -q
./feature-tracking/tracker.py validate
./feature-tracking/tracker.py show audit_ledger
./feature-tracking/tracker.py check audit_ledger
```
