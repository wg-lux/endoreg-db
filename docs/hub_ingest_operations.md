# Hub-Ingest-Betriebshandbuch

Dieses Dokument ist das Betriebs- und Incident-Runbook für den kontrollierten
Site-Node-zu-Central-Hub-Ingest. Der Fertigstellungsstatus wird ausschließlich
in [`feature-tracking/done/HubIngest.yml`](../feature-tracking/done/HubIngest.yml)
geführt.

Für Implementierer und Reviewer von Sendern beschreibt
[`hub_transfer_typed_contract.md`](hub_transfer_typed_contract.md) den aktuell
verbindlichen, typisierten Wire-Vertrag `3.0`, vollständige Video- und
Reportbeispiele, den mTLS-Transporttyp und die Portierung älterer
`data-transfer-nginx-mtls`-Ansätze. Eine vollständige
[`englische Fassung`](hub_transfer_typed_contract.en.md) ist ebenfalls
verfügbar.

Das produktionskritische Import-Monitoring einschließlich transienter Fehler,
Quarantäne und HTTP-Live-Streaming-(HLS)-Materialisierung wird ausschließlich
in [`feature-tracking/ImportMonitoring.yml`](../feature-tracking/ImportMonitoring.yml)
bewertet. Dieses Runbook beschreibt den Betrieb, führt aber keinen parallelen
Fertigstellungsstatus.

## Import-Monitoring und Zustandsachsen

Import, Anonymisierung, HLS-Materialisierung und Cleanup sind voneinander
unabhängige Zustandsachsen. Ein erfolgreich importiertes oder anonymisiertes
Video ist deshalb nicht automatisch streambereit; ebenso ändert ein
HLS-Fehler nicht rückwirkend den Importstatus.

| Importzustand | Bedeutung | Automatik | Bedieneraktion |
| --- | --- | --- | --- |
| `pending` | Persistiert und wartet auf Verarbeitung | Worker-Dispatch | Bei ungewöhnlichem Alter Worker und Queue prüfen |
| `processing` | Ein Worker verarbeitet den Import | keine parallele Verarbeitung | Bei Überschreitung der betrieblichen Laufzeitschwelle eskalieren |
| `retrying` | Transienter Dispatchfehler, Quelle bleibt geschützt erhalten | begrenzter exponentieller Retry | Bis `next_retry_at` abwarten; Versuchszähler beobachten |
| `anonymized` | Importverarbeitung erfolgreich | keine | Separate Anonymisierungs-, HLS- und Cleanup-Achsen prüfen |
| `error` | Terminaler, stabil codierter Fehler | keine automatische Wiederholung | Fehlercode prüfen; Konfiguration korrigieren oder sicheren Neuimport planen |
| `lost` | Quelle oder Ledger ist inkonsistent | fail-closed | Logs und Storage sichern, niemals manuell auf Erfolg setzen |
| Quarantäne | Geschützte Quelle wurde aus dem aktiven Importfluss isoliert | keine automatische Löschung | Ledger abgleichen und Reviewentscheidung dokumentieren |

Erlaubte Hauptübergänge sind `pending -> processing -> anonymized`,
`processing -> retrying -> processing` und nach ausgeschöpften Versuchen
`retrying -> error`. `lost` ist terminal. Ein Quarantäneeintrag besitzt einen
eigenen Review-Lebenszyklus und überschreibt keinen Upload-Job-Status.

Die Retry-Policy startet bei 30 Sekunden, verdoppelt die Verzögerung je
Versuch, ist auf 15 Minuten begrenzt und erlaubt standardmäßig drei Versuche.
Der periodische Task `endoreg_db.retry_due_upload_jobs` übernimmt fällige Jobs
unter Datenbanksperre und übergibt sie idempotent an die Pipeline-Queue.

Stabile Importfehlercodes:

| Code | Bedeutung und nächster Schritt |
| --- | --- |
| `dispatch_unavailable` | Transient; automatischen Retry abwarten, bei Erschöpfung Queue oder Broker eskalieren |
| `duplicate_content` | Kein Neuimport; vorhandene validierte Daten bleiben maßgeblich |
| `invalid_configuration` | Terminal; Center-, Worker- oder Laufzeitkonfiguration korrigieren |
| `invalid_input` | Terminal; Eingangsvertrag korrigieren und sicher neu importieren |
| `media_integrity_failed` | Terminal; Quelle und Quarantäne prüfen, keine unsichere Wiederherstellung |
| `processing_failed` | Terminal; geschützte strukturierte Logs über Upload-Job-ID korrelieren |
| `source_missing` | Terminal beziehungsweise `lost`; Storage und Ledger abgleichen |

Die Monitoring-API liefert ausschließlich freigegebene Bedienertexte. Absolute
Pfade, Hashes, Stacktraces, Rohmedien und technische Ausnahmetexte verbleiben in
zugriffsgeschützten Logs. Die Anonymisierungsübersicht erkennt insbesondere
Duplikate nur über `error_code`, nie durch Textsuche.

### HLS-Materialisierung

Raw- und Processed-HLS werden getrennt als `queued`, `materializing`, `ready`
oder `failed` gezeigt. Jeder Eintrag enthält die Upload-Job-Korrelation, eine
opaque Quellgeneration, die Zielgeneration, Segmentzahl und Zeitpunkte.
`ready` ist erst nach validierter, atomarer Veröffentlichung einer vollständigen
Playlist-, Schlüssel- und Segmentgeneration zulässig. Bei Fehlern wird ein
stabiler Code (`dispatch_failed`, `materialization_failed`,
`inconsistent_artifact` oder `stale_attempt`) ausgegeben; eine vorherige valide
Generation wird wiederhergestellt und nicht gelöscht. Wiedergabe- und
Segment-Update-Leases sowie die Details der atomaren Veröffentlichung sind im
kanonischen [`video_storage_normalization.md`](video_storage_normalization.md)
definiert.

### Diagnose und Wiederanlauf

```sh
python manage.py check_system_health --json
python manage.py materialize_video_hls --artifact-kind raw --json
python manage.py materialize_video_hls --artifact-kind processed --json
python manage.py reap_quarantine --older-than-days 30 --dry-run --json
```

Der Health-Check meldet unter anderem wartende und fällige Retries,
ausgeschöpfte Versuche, `ERROR`, `LOST`, fehlgeschlagene oder hängende
HLS-Materialisierungen, Quarantänealter und freien Speicher. Vor manueller
Wiederholung muss geprüft werden, dass kein aktiver Job und keine aktive
Media-Operation-Lease konkurriert. Quarantäne wird zuerst synchronisiert und
reviewt; Löschung erfolgt ausschließlich nach expliziter Freigabe und separatem
Reap-Schritt.

## Geltungsbereich und Sicherheitsphase

Der produktive Transfervertrag befindet sich in Phase 1:

- Transportverschlüsselung und Node-Authentifizierung erfolgen über mTLS.
- Ein zusätzliches `NetworkNode`-Shared-Secret authentifiziert Requests; es ist
  kein Verschlüsselungsschlüssel.
- Übertragen werden ausschließlich anonymisierte, verarbeitete Medien.
- Raw-Medien werden weder exportiert noch vom Transfer-Endpunkt akzeptiert.
- Der langlebige Master-Key bleibt in der lokalen Secret- beziehungsweise
  Storage-Grenze und darf weder in lx-annotate konfiguriert noch übertragen
  werden.

Envelope Encryption ist die gesperrte Phase 2. Solange kein Empfänger-Public-Key,
kein pro Transfer erzeugter DEK und kein getesteter Unwrap-Pfad implementiert
sind, darf Phase 1 nicht auf eigenständige, außerhalb des geschützten
mTLS-Transfers verteilte Artefakte ausgeweitet werden.

## Rollen- und Ingressmatrix

| Rolle | Lokaler Watcher | Benutzer-Upload | Hub-Transfer-Receiver | Outbound zum Hub |
| --- | --- | --- | --- | --- |
| `standalone` | erlaubt | kompatibler lokaler Modus | deaktiviert (`404`) | nicht vorgesehen |
| `site_node` | erlaubt | kompatibler lokaler Modus | deaktiviert (`404`) | über lx-annotate erlaubt |
| `central_hub` | vertrauenswürdiger lokaler Pfad | authentifiziert und `center_key`-pflichtig | aktiviert | nicht vorgesehen |
| `local_study_server` | nur freigegebene preanonymisierte Imports | authentifiziert und `center_key`-pflichtig | deaktiviert (`404`) | kontrollierter Exportpfad |

API, Watcher und Transfer benutzen getrennte Eingangsgrenzen, konvergieren aber
auf persistierte Upload-/Transfer-Ledger, Content-Hashes, Center-Auflösung,
Provenienz und explizite Retention-Zustände.

Der Hub-Transfer-Receiver ist ein Machine-to-Machine-Endpunkt: Registrierung,
Status und Upload authentifizieren immer den `NetworkNode` und übernehmen den
Center-Scope ausschließlich aus dessen `owning_center`. Eine Django-Sitzung ist
weder erforderlich noch eine alternative Berechtigung; eine vorhandene Sitzung
darf die Node-Grenze nicht erweitern oder einschränken.

## Central-Hub-Konfiguration

Für den Central Hub sind mindestens folgende Werte erforderlich:

```sh
ENDOREG_DEPLOYMENT_ROLE=central_hub
DJANGO_DEBUG=false
DB_ENGINE=django.db.backends.postgresql
DJANGO_SECURE_PROXY_SSL_HEADER_NAME=HTTP_X_FORWARDED_PROTO
DJANGO_SECURE_PROXY_SSL_HEADER_VALUE=https
ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT=true
ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true
ENDOREG_HUB_TRANSFER_MTLS_META_KEY=HTTP_X_CLIENT_CERT_VERIFIED
ENDOREG_HUB_TRANSFER_MTLS_META_VALUE=SUCCESS
```

Der Reverse Proxy muss eingehende, vom Client gesetzte Forwarded- und
mTLS-Attestation-Header entfernen. Er setzt sie erst nach erfolgreicher TLS-
beziehungsweise Client-Zertifikatsprüfung. Der Django-Prozess darf bei diesem
Vertrauensmodell nicht direkt aus einem ungeschützten Netz erreichbar sein.

Speicher-, I/O- und Quarantänepfade müssen innerhalb der verschlüsselten lokalen
Runtime-Grenze liegen. Die Anwendung prüft die Pfadgrenze; Verschlüsselung,
Mount-Reihenfolge, Eigentümer und Berechtigungen bleiben Aufgabe des Hosts.

## Center- und Node-Provisionierung

1. Center mit unveränderlichem, maschinenlesbarem `center_key` anlegen.
2. Genau einen aktiven lokalen `central_hub`-Node und je Sender einen aktiven
   `site_node` mit `owning_center` anlegen.
3. Für jeden Sender ein zufälliges Request-Secret über den vorhandenen
   `NetworkNode.set_shared_secret(...)`-Pfad setzen. Persistiert wird nur der
   Passwort-Hash.
4. Das Klartext-Secret ausschließlich im Secret-Store des Senders hinterlegen;
   nicht in Git, Datenbank, Frontend oder Logs.
5. Sender-`base_url` und Empfängerziel auf `https://` begrenzen und einen
   Probe-Transfer mit einem anonymisierten Testartefakt durchführen.

Die Administration-Oberfläche von lx-annotate zeigt Node-, Transport- und
Transferbereitschaft, aber niemals Private Keys oder Shared Secrets.

## Zertifikatsrotation

1. Neue CA-/Server-/Client-Zertifikate mit Überlappungsfenster bereitstellen.
2. CA-Vertrauen zuerst auf Hub und Sendern aktualisieren.
3. Client-Zertifikat und privaten Schlüssel als neue, nur lesbare Secret-Dateien
   ausrollen.
4. Dienste neu laden und in der Administration die mTLS-Bereitschaft prüfen.
5. Erfolgreichen Metadata-plus-Processed-Media-Transfer ausführen und dessen
   Remote-Acknowledgement kontrollieren.
6. Erst danach alte Zertifikate sperren und entfernen.

Fehlt Zertifikatsmaterial oder die Proxy-Attestation, muss der Transfer
fail-closed mit `403` enden. Shared-Secret-only ist kein zulässiger Ersatz.

## Transferprüfung und Korrelation

Für eine vollständige Nachverfolgung werden folgende Identitäten verwendet:

- `outbound_job_id` auf dem Sender,
- `transfer_key` als idempotente fachliche Transferidentität,
- `TransferJob.id` beziehungsweise `remote_transfer_id` auf dem Hub,
- `resource_hash` als Inhaltsidentität,
- `source_center_key`, `source_node_key` und `target_node_key`,
- `local_cleanup_status` und Hub-`cleanup_status`.

Die lx-annotate-Administration zeigt lokale und Remote-Korrelation sowie den
Cleanup-Zustand. Sender-Logs unter `lx_annotate.hub_export.audit`, Hub-Logs unter
`endoreg_db.hub.audit` und strukturierte Dateisystemereignisse unter
`endoreg_db.utils.file_operations` müssen sich über diese IDs verbinden lassen.

Ein erfolgreicher Transfer endet erst mit einem Hub-Acknowledgement
`transfer_status=applied`. `awaiting_media` ist ein Zwischenzustand;
`failed`, `inconsistent`, Hashabweichungen und widersprüchliche Snapshots sind
Incident-Zustände und dürfen nicht automatisch als Erfolg behandelt werden.

Das Acknowledgement enthält zusätzlich den erwarteten Hash des anonymisierten
Processed-Mediums. Der Sender muss Transfer-ID, Transfer-Key, Quell- und
Zielknoten, Quellzentrum, Ressourcenart, Ressourcenhash, Processed-Media-Hash,
Transfermodus und Payload-Schemaversion gegen seinen unveränderlichen lokalen
Auftrag prüfen. Erst ein vollständig passendes `applied` darf den lokalen
Auftrag abschließen oder Cleanup-fähig machen. Abweichungen sind terminale
Integritätsfehler und werden nicht automatisch wiederholt.

## Cleanup und Quarantäne

- Upload-Quellen werden nur bei `cleanup_status=eligible` entfernt.
- `preserve_source` bleibt erhalten; `delete_after_success` wird erst nach
  erfolgreicher Medienintegritätsprüfung cleanup-fähig.
- Outbound-Processed-Media wird standardmäßig behalten. Eine Policy
  `eligible_after_verified_apply` markiert lediglich die lokale Freigabe nach
  bestätigtem `applied`; sie löscht nicht unkontrolliert die einzige Kopie.
- Transfer-Cleanup auf dem Hub bleibt explizit als Operator-Intent oder
  `not_requested` nachvollziehbar.
- Quarantäne-Löschung benötigt eine dokumentierte Review-Entscheidung,
  anschließende Freigabe und einen separaten Reap-Schritt.

Beispiel für die sichere Quarantänefolge:

```sh
python manage.py reap_quarantine --older-than-days 30 --dry-run --json
python manage.py reap_quarantine --older-than-days 30 --approve-stale --decision-reason "retention period elapsed" --json
python manage.py reap_quarantine --older-than-days 30 --confirm --json
```

Bei Hashfehlern, fehlenden Artefakten oder widersprüchlicher Provenienz zuerst
Quarantäne und Ledger sichern. Keine Datei manuell löschen und keinen Status in
der Datenbank auf Erfolg setzen.

## Monitoring, Kapazität und Alarmierung

Mindestens zu alarmieren sind:

- Hub-Konfiguration oder mTLS-Material nicht bereit,
- `failed`/`inconsistent` Transfers und ausgeschöpfte Retries,
- `ERROR`/`LOST` Upload-Jobs und hängende Processing-Zustände,
- wachsende oder überalterte Quarantäne,
- freier Speicher unter dem betrieblich festgelegten Grenzwert,
- nicht verifizierte Audit-Ledger-Integrität,
- wiederholte Auth-, mTLS-, Hash- oder Payload-Ablehnungen.

Die Administration aktualisiert das Transfer-Monitoring automatisch. Für
den allgemeinen Import- und Speicherzustand ist
`python manage.py check_system_health --json` zu verwenden. Der Zustand des
Outbound-Hub-Transfers wird separat geprüft:

```sh
python manage.py check_hub_export_health
python manage.py check_hub_export_health --no-fail-on-attention
systemctl status lx-annotate-hub-export-health.service
systemctl status lx-annotate-hub-export-health.timer
journalctl -u lx-annotate-hub-export-health.service
```

Der erste Befehl gibt kompaktes JSON aus und endet ungleich null, sobald eine
terminale Konfigurationsablehnung, Autorisierungsablehnung,
Integritätsinkonsistenz, ein ausgeschöpfter Retry oder ein unklassifizierter
Fehler vorliegt. Ein transienter Fehler bleibt bis zur Retry-Erschöpfung
nichtkritisch sichtbar. Die LuxNix-Site-Node-Konfiguration führt diese Prüfung
periodisch als persistierenden systemd-Timer aus; eine fehlgeschlagene Unit und
das strukturierte Ereignis `hub_export.health_snapshot` bilden den lokalen
Alarmzustand. `--no-fail-on-attention` ist nur für eine lesende Diagnose
gedacht und unterdrückt nicht die persistierte Fehlerklasse.

Die Exportübersicht zeigt dieselben persistierten Klassen pro Transfer in
deutscher Bedienersprache sowie aktive, abgeschlossene und aufmerksamkeits-
pflichtige Transfers. Das Health-JSON und die Alarmereignisse enthalten keine
Fehlertexte, Secrets, Rohmedien oder absoluten Medienpfade. HTTP `401` und
`403` vom Hub sind terminale Autorisierungsablehnungen und werden nicht als
transiente Netzwerkfehler wiederholt. Kapazitätsgrenzen müssen an erwartete
parallele Uploads, temporäre Kopien, Quarantäne, verarbeitete Derivate und
Backup-Fenster angepasst werden.

Für gekoppelte Hub-Backups erzwingt LuxNix zusätzlich
`services.luxnix.lxAnnotateLocal.hub.backup.minimumFreeBytes`. Der Standardwert
reserviert 10 GiB auf dem Snapshot-Dateisystem. Der Backup-Dienst prüft die
Reserve vor dem Staging und erneut, nachdem Datenbank-Dump und Medien in den
noch nicht publizierten Snapshot kopiert wurden. Unterschreitet der freie
Speicher die Reserve, schlägt die Unit fehl, entfernt ausschließlich den
unvollständigen Pending-Snapshot und lässt den bisherigen `latest`-Restore-Punkt
unverändert. Der Grenzwert darf anhand dokumentierter Kapazitätsplanung erhöht
werden; eine Absenkung benötigt eine begründete Betriebsentscheidung und darf
nicht als Reaktion auf einen bereits vollen Datenträger erfolgen.

```sh
systemctl status lx-annotate-hub-backup.service
journalctl -u lx-annotate-hub-backup.service | grep -F minimum_free_bytes
df -B1 /var/lib/lx-annotate/data/hub/backup/snapshots
```

## Backup und Restore

Ein Hub-Backup besteht immer aus zwei zusammengehörigen Teilen:

1. konsistentes datenbanknatives PostgreSQL-Backup,
2. Backup der verschlüsselten, geschützten Media-/Quarantäne-Grenze inklusive
   Storage-Namen und Berechtigungen.

LuxNix publiziert diese Teile als einen Restore-Punkt. Ein manueller oder vom
Timer ausgelöster Start von `lx-annotate-hub-backup.service` startet zuerst
`postgresqlBackup.service`. Nur wenn der komprimierte PostgreSQL-Dump
erfolgreich und lesbar vorliegt, reicht systemd ihn als private Credential an
den unprivilegierten Hub-Backup-Dienst weiter. Der Dienst prüft das
Gzip-Format, kopiert den Dump als `database/all.sql.gz` in den noch nicht
publizierten Medien-Snapshot und nimmt ihn in dieselbe Prüfsummenliste und das
JSON-Manifest auf. Erst danach wechselt der atomare `latest`-Symlink auf den
neuen Restore-Punkt. Ein fehlender, beschädigter oder fehlgeschlagener
Datenbank-Dump lässt den bisherigen `latest`-Restore-Punkt unverändert.

Bedienerprüfung auf dem Central Hub:

```sh
sudo systemctl start lx-annotate-hub-backup.service
sudo systemctl status postgresqlBackup.service lx-annotate-hub-backup.service
sudo readlink -f /var/lib/lx-annotate/data/hub/backup/snapshots/latest
sudo jq '.database_dump' /var/lib/lx-annotate/data/hub/backup/manifests/*.json
sudo sh -c 'cd /var/lib/lx-annotate/data/hub/backup/snapshots/latest && sha256sum --check ../../manifests/$(basename "$(readlink -f .)").sha256'
```

Der ausgewählte Manifest-Eintrag muss für `database_dump.relative_path` den
Wert `database/all.sql.gz`, für `format` den Wert
`postgresql-pg_dumpall-sql-gzip` und einen zum Snapshot passenden SHA-256-Hash
enthalten. Das Manifest und die Prüfsummenliste gehören immer zu dem
Zeitstempel, auf den `latest` zeigt; nicht mehrere Zeitstempel kombinieren.

Restore-Probe:

1. Den Zielpfad von `latest`, das gleichnamige JSON-Manifest und die
   gleichnamige Prüfsummenliste als untrennbares Set auswählen und vor dem
   Restore vollständig mit `sha256sum --check` validieren.
2. In einer isolierten Umgebung `database/all.sql.gz` mit PostgreSQL-Werkzeugen
   wiederherstellen und anschließend den übrigen Snapshot als geschützte
   Medien-/Quarantäne-Grenze desselben Sicherungszeitpunkts einspielen.
3. Migrationen ausführen und die Central-Hub-Produktionschecks starten.
4. Audit-Ledger-Integrität aktualisieren und System-Health prüfen.
5. Stichprobenweise `resource_hash`, Transfer-Snapshot, gespeichertes Medium und
   Acknowledgement verbinden.
6. Fehlende oder nicht lesbare Artefakte als `LOST`/inkonsistent behandeln und
   Logs erhalten; keine unsichere Auto-Recovery durchführen.

Eine Datenbank ohne zugehörige Medien oder Medien ohne konsistente Ledger sind
kein erfolgreicher Restore.

## Incident-Ablauf

1. Betroffene Korrelationen, Zeitfenster, Nodes und Center erfassen.
2. Transfer stoppen beziehungsweise Sender-Node deaktivieren, wenn Auth- oder
   Integritätsverletzung vermutet wird.
3. Strukturierte Audit-, Proxy- und Worker-Logs sichern; Secrets und klinische
   Payloads nicht in Tickets kopieren.
4. Quarantäne und Speicherbelegung prüfen, aber nichts manuell entfernen.
5. Ursache beheben: Zertifikat/Proxy, Center-Scope, Hash, Snapshot, Kapazität,
   Storage oder Worker.
6. Nur idempotent über denselben `transfer_key` wiederholen. Ein neuer Key ist
   ausschließlich für einen fachlich neuen Transfer zulässig.
7. Remote-`applied`, Medienintegrität und Cleanup-Entscheidung abschließend
   dokumentieren.

## Verifikationscheckliste

- Central-Hub-Startup scheitert ohne PostgreSQL, HTTPS-Proxyvertrag oder mTLS.
- Unauthentifizierte, fremd-Center- und Raw-Media-Anfragen werden abgelehnt.
- Wiederholte Registrierung und Medienübertragung bleiben idempotent.
- Manipulierte Medien und widersprüchliche Metadaten enden inkonsistent oder in
  Quarantäne, nicht als Erfolg.
- Sender- und Hub-Acknowledgement stimmen für Transfer-, Ressourcen- und
  Cleanup-Identität überein.
- Administration zeigt aktive, erfolgreiche und fehlgeschlagene Transfers samt
  Korrelation und Cleanup-Zustand.
- Quarantäne-Dry-Run, explizite Freigabe und Reap wurden geprobt.
- Datenbank-und-Medien-Restore wurde isoliert geprüft.

## Referenzen

- [`docs/wiki/hub_ingest_current_state.md`](wiki/hub_ingest_current_state.md)
- [`docs/deployment_note_hub_contract.md`](deployment_note_hub_contract.md)
- [`endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`](../endoreg_db/import_files/multi_centre_storage_hub_roadmap.md)
