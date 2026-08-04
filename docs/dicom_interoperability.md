# DICOM-Interoperabilität: Integrationsvertrag und Runbook

## Zweck und Freigabegrenze

`endoreg_db` katalogisiert pseudonymisierte DICOM-Exporte aus
`lx-anonymizer` anhand des strikt validierten Manifestvertrags Version 2. Die
Artefakte müssen bereits anonymisierte, verarbeitete Medien innerhalb der
geschützten Storage-Grenze sein. Vor jeder Datenbankänderung prüft ein vom
aufrufenden Storage-Adapter bereitgestellter Verifier SHA-256 und Dateigröße.

Der aktuelle Stand ist ein Import- und Metadatenkatalog, kein vollständiger
DICOM-Knoten. Insbesondere gehören folgende Abläufe noch nicht zum
freigegebenen Umfang:

- Erzeugung von DICOM-Dateien aus MP4 oder anderen Rohformaten
- C-STORE, C-FIND, C-MOVE oder andere DIMSE-Dienste
- DICOMweb mit STOW-RS, QIDO-RS oder WADO-RS
- PACS-Routing und automatische Übertragung an externe Systeme
- FHIR-Write oder bidirektionale Synchronisation
- Export von Rohmedien, direkten Patientenidentifikatoren oder Schlüsseln

Es wird gegenwärtig die im Endoskopie-Testvertrag verwendete Video Endoscopic
Image Storage SOP Class `1.2.840.10008.5.1.4.1.1.77.1.1.1` mit Explicit VR
Little Endian `1.2.840.10008.1.2.1` nachgewiesen. Das Schema akzeptiert weitere
syntaktisch gültige SOP- und Transfer-Syntax-UIDs, doch diese gelten ohne
separaten Integrationsnachweis nicht als betrieblich freigegeben.

## Manifestvertrag Version 2

Der Import erfolgt über
`import_dicom_export_manifest(patient_examination, payload, artifact_verifier)`.
Pflichtbestandteile sind:

- `schema_version: 2`, eine UUID als `export_id`, ein zeitzonenbehafteter
  `created_at`-Zeitpunkt und `source_system`
- ein Deidentifikationsnachweis mit `patient_identity_removed: true` und der
  Artefaktklasse `anonymized_processed`
- ein erfolgreiches vorgelagertes Validierungsergebnis
- Study-, Series- und SOP-Instance-UIDs sowie Transfer-Syntax-UIDs
- relative Storage-Referenz, SHA-256 und positive Dateigröße je Instanz

Unbekannte Felder sind verboten. Absolute Pfade und `..`-Segmente werden
abgewiesen. Direkte Original-Identifikatoren passen nicht in den Vertrag und
führen zu einem Validierungsfehler. Das Manifest enthält Metadaten und
Storage-Referenzen, aber weder DICOM-Nutzdaten noch kryptografische Schlüssel.

`export_id` identifiziert einen Export idempotent. Ein identisches Manifest für
dieselbe Untersuchung ist ein erfolgreicher Replay und erzeugt keine weiteren
Datensätze. Eine wiederverwendete Export-ID mit anderem Inhalt oder andere
bereits belegte Study-, Series- oder SOP-Instance-UIDs führen zu einem lauten
Konflikt. Teilpersistenz wird durch eine Datenbanktransaktion verhindert.

## Deployment und Migration

Vor Aktivierung des Imports muss die Django-Migration
`0046_dicom_interoperability` angewendet sein. Artefakte bleiben innerhalb der
lokalen verschlüsselten Storage-Grenze. Ein aufrufender Adapter muss die
relative `artifact_reference` ausschließlich dort auflösen und Hash sowie
Größe prüfen. Der Service selbst verschiebt oder kopiert keine Dateien.

Es gibt keine DICOM-spezifischen Umgebungsvariablen und keinen unsicheren
Fallback. Sobald eine spätere Ausbaustufe Daten zwischen Knoten transportiert,
gelten mTLS und die Envelope-Encryption-Vorgaben des jeweiligen
Deploymentprofils; der Master-Key darf nie übertragen werden.

### Versions- und Backfillvertrag

Die Runtime akzeptiert ausschließlich Manifestversion 2. Eine fehlende,
stringförmige oder unbekannte `schema_version` wird vor der weiteren
Payloadvalidierung mit der unterstützten Version abgewiesen. Insbesondere wird
Version 1 nicht implizit ergänzt oder geraten; dafür existiert kein sicher
definierter Quellvertrag.

Bestehende V2-JSON-Datensätze werden mit folgendem Command geprüft:

```text
python manage.py backfill_dicom_manifest_v2
```

Der Default ist ein schreibfreier Dry-run. Er validiert jeden Datensatz,
vergleicht `export_id` mit dem Primärschlüssel und meldet, wie viele Manifeste
kanonisiert werden müssten. `--apply` sperrt die betroffenen Zeilen und schreibt
kanonisches Manifest, Version und SHA-256 gemeinsam in genau einer
Datenbanktransaktion. Ein einziger unbekannter oder ungültiger Datensatz bricht
die gesamte Kohorte ab; Teilupdates werden zurückgerollt. Fehlermeldungen
enthalten nur einen gehashten Datensatzbezug und niemals Manifestinhalt,
Patientenpseudonym, DICOM-UID oder Artefaktpfad.

Die Deploymentprobe verwendet
`tests/fixtures/dicom_manifest_v2_existing.json` als versionierten
Bestandsdatensatz und weist Dry-run, Apply, vollständigen Transaktionsrollback
und die klare Ablehnung unbekannter Versionen nach. Sie läuft über
`devenv tasks run quality:type-safety-operational`.

### Rollout, Kompatibilitätsdauer und Rollback

Vor `--apply` wird ein verschlüsseltes Datenbankbackup der betroffenen
`DicomExportJob`-Zeilen nach dem freigegebenen Datenbank-Runbook erstellt. Der
Dry-run muss ohne Validierungsfehler enden. Die Ausführung erfolgt in einem
Wartungsfenster; Abbruchkriterien sind jeder Versions-/Validierungsfehler, eine
unerwartete Anzahl zu ändernder Datensätze oder ein Digest-Konflikt. Erst nach
erfolgreichem Apply werden Importworker wieder freigegeben.

Der Backfill verändert nur die kanonische Darstellung desselben V2-Vertrags.
Ein Code-Rollback ist deshalb ohne Datenrückmigration möglich. Falls dennoch
die vorherige JSON-Darstellung benötigt wird, werden ausschließlich
`schema_version`, `manifest` und `manifest_sha256` aus dem verschlüsselten
Backup wiederhergestellt; Artefakte und Schlüssel werden nicht kopiert.

Version 2 bleibt mindestens zwölf Monate nach der ersten produktiven Freigabe
einer späteren Version lesbar. Ein konkretes V2-Enddatum darf erst gemeinsam
mit dem Nachfolgeschema, dessen explizitem Backfill und einer erfolgreichen
Rollbackprobe festgelegt werden. Bis dahin gibt es kein V2-Enddatum; spätere
unbekannte Versionen schlagen weiterhin fail-closed fehl.

## Beobachtbarkeit

Der Logger `endoreg_db.interoperability.dicom` erzeugt strukturierte Ereignisse:

| Ereignis | Bedeutung | Erwartete Reaktion |
| --- | --- | --- |
| `dicom.import_completed` | Import vollständig committed | Export als verarbeitet bestätigen |
| `dicom.import_replayed` | identischer Export bereits importiert | als erfolgreichen idempotenten Retry behandeln |
| `dicom.import_rejected` / `invalid_manifest` | Schema oder Datenschutzvertrag verletzt | Payload beim Sender korrigieren; nicht blind wiederholen |
| `dicom.import_rejected` / `artifact_integrity_failed` | Hash oder Größe stimmt nicht | Artefakt isolieren, Quelle und geschützten Storage prüfen |
| `dicom.import_rejected` / `identity_conflict` | Export- oder DICOM-UID kollidiert | keine UID umschreiben; Konflikt fachlich untersuchen |
| `dicom.import_rejected` / `concurrent_identity_conflict` | paralleler Import kollidiert | Zustand prüfen und anschließend identisch wiederholen |

Zur Korrelation enthalten Ereignisse nur SHA-256-gehashte Export- und
Untersuchungsbezüge. Patientenpseudonyme, DICOM-UIDs und Artefaktpfade werden
nicht als Betriebsfelder protokolliert.

Empfohlenes Monitoring:

- Alarm auf jede Zunahme von `dicom.import_rejected`, gruppiert nach `reason`
- Warnung bei wiederholten `artifact_integrity_failed` aus derselben Quelle
- Dashboard für `completed`, `replayed` und `rejected`
- periodische Prüfung, dass die strukturierte Produktions-Logging-Konfiguration
  die Ereignisse im vorgesehenen geschützten Logziel erfasst

## Wiederanlauf und Betriebsübung

1. Migrationen und Erreichbarkeit des geschützten Storages prüfen.
2. Einen bekannten, anonymisierten Testexport erfolgreich importieren und das
   `dicom.import_completed`-Ereignis nachweisen.
3. Dasselbe Manifest erneut senden. Es muss `dicom.import_replayed` entstehen;
   Study, Series und Instance dürfen nicht dupliziert werden.
4. Eine Testkopie mit abweichendem Hash verwenden. Der Import muss vor jeder
   Persistenz mit `artifact_integrity_failed` scheitern.
5. Nach Reparatur des Artefakts exakt dasselbe ursprüngliche Manifest erneut
   importieren. Es muss vollständig erfolgreich sein.
6. Datenbankanzahl, strukturierte Ereignisse und den aufrufenden Jobstatus
   gemeinsam dokumentieren.

Automatische Wiederholung ist nur für unveränderte Payloads sinnvoll.
Validierungs- und Identitätskonflikte erfordern zuerst eine fachliche Klärung.
Artefakte mit Integritätsfehlern dürfen nicht automatisch als gültig markiert,
umbenannt oder durch ungesicherte Quellen ersetzt werden.
