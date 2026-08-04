# Implementierungsleitlinie für stabile parallele Berichtsimporte

Diese Leitlinie beschreibt die Zielarchitektur für den PDF-Berichtsimport in
`endoreg-db` und den Aufruf von `lx-anonymizer`. Sie ist eine technische
Vorgabe, keine Fortschrittsliste. Umfang, Abnahmekriterien und
Produktionsfreigabe werden ausschließlich in
`feature-tracking/Reporting.yml` gepflegt.

Die komplementäre Vorgabe für die Bibliotheksseite liegt im Repository
`lx-anonymizer` unter
`docs/report_pipeline_concurrency_contract.md`.

## Ziel

Ein Berichtsimport muss bei parallelen Prozessen, Prozessabbruch,
Quelländerung, Retry und identischen Uploads deterministisch bleiben. Er darf
höchstens einen kanonischen anonymisierten Bericht veröffentlichen und niemals
ein teilweise geschriebenes Artefakt als erfolgreich markieren.

Native Rust-Funktionen sollen große lokale Dateioperationen außerhalb des
Python Global Interpreter Lock ausführen. Rust ersetzt dabei nicht die
fachlichen Zustandsübergänge, Datenbanktransaktionen oder
Speicherberechtigungen von `endoreg-db`.

## Geltungsbereich und Nicht-Ziele

Diese Leitlinie gilt für:

- `ReportImportService.import_and_anonymize(...)`;
- die Erzeugung eines sensiblen Arbeitssnapshots;
- inhaltsbezogene Deduplizierung und Wiederholungen;
- den Aufruf von `lx_anonymizer.ReportReader`;
- Prüfung und Publikation des anonymisierten PDF;
- Cleanup, Reconciliation, Beobachtbarkeit und Paketierung des nativen Moduls.

Nicht Bestandteil sind:

- Änderungen der klinischen Erkennungs- oder Redaktionslogik;
- ein Export roher Berichte;
- eine Aufweichung der verschlüsselten Speichergrenze;
- die Verwendung von `NetworkNode.shared_secret` zur Nutzdatenverschlüsselung;
- ein zweites Status- oder Roadmap-Dokument neben dem Feature Tracker.

## Verbindliche Invarianten

1. Rohdaten und sensible Arbeitssnapshots bleiben innerhalb der freigegebenen
   verschlüsselten Speichergrenze.
2. `endoreg-db` besitzt Quellannahme, Dateianspruch, Snapshot, Sperren,
   Persistenz, Deduplizierung, kanonische Publikation und Cleanup.
3. `lx-anonymizer` besitzt ausschließlich die inhaltliche Extraktion und
   Anonymisierung eines bereits zugewiesenen Snapshots.
4. Ein erfolgreicher Datenbankzustand darf erst nach PDF-Validierung,
   Hashprüfung und atomarer Publikation sichtbar werden.
5. Ein Versuch schreibt ausschließlich in ein eindeutiges Versuchsverzeichnis.
   Globale oder nur aus dem Inhaltshash abgeleitete temporäre Ausgabepfade sind
   unzulässig.
6. Ein älterer, abgelaufener oder ersetzter Worker darf keinen neueren Versuch
   finalisieren.
7. Ungültige oder inkonsistente Zustände brechen laut ab. Es gibt keine stille
   Reparatur und keinen stillen Wechsel auf ein schwächeres Sicherheitsprofil.
8. Alle von `endoreg-db` veranlassten Dateisystemmutationen laufen über die
   typisierten Wrapper in `endoreg_db.utils.filesystem.file_operations` und
   erzeugen strukturierte JSON-Ereignisse.

## Zielablauf

Der Sollablauf besitzt eine feste Reihenfolge:

1. Ein Produzent schreibt unter einem nicht beobachteten temporären Namen,
   synchronisiert Datei und Verzeichnis und benennt anschließend atomar auf
   `*.pdf` um.
2. Der Importer validiert Pfad, regulären Dateityp, Speichergrenze und
   Dateiendung, ohne dem Dateinamen fachlich zu vertrauen.
3. Der Worker beansprucht den Quellpfad.
4. Ein typisierter Dateisystem-Wrapper ruft die native Snapshot-Funktion auf.
   Sie liest dieselbe geöffnete Quelldatei, kopiert und hasht sie in einem
   Durchlauf und veröffentlicht den Snapshot atomar im eindeutigen
   Versuchsverzeichnis.
5. Mit dem zurückgegebenen SHA-256-Hash wird die inhaltsbezogene Sperre
   beziehungsweise das Datenbank-Fencing erworben.
6. Innerhalb einer kurzen Datenbanktransaktion wird ein Importversuch
   idempotent angelegt oder ein bereits nutzbares Ergebnis gefunden.
7. `lx-anonymizer` verarbeitet ausschließlich den unveränderlichen sensiblen
   Snapshot und schreibt ausschließlich in das Versuchsverzeichnis.
8. `endoreg-db` validiert Ergebnisformat, Größe, Hash, Provenienz und
   Vertragsversion.
9. Das Ergebnis wird atomar unter dem kanonischen Ziel veröffentlicht.
10. Der Datenbankzustand wird nur mit dem aktuellen Fencing-Token erfolgreich
    abgeschlossen.
11. Nichtkanonische Versuchsartefakte und die Importquelle werden gemäß
    Lebenszyklusregeln entfernt.

Die Sperrreihenfolge lautet immer:

```text
Quellpfadanspruch
  -> stabiler Snapshot
    -> Inhaltsanspruch
      -> kurze Datenbanktransaktion
        -> Anonymisierungsversuch
          -> validierte atomare Publikation
```

Kein untergeordneter Schritt darf eine vorherige Sperrebene in umgekehrter
Reihenfolge anfordern.

## Nativer Snapshot-Vertrag

Die Rust-Implementierung gehört in
`rust/endoreg_rust_backend`. Der Python-Aufruf gehört hinter einen typisierten
Dateisystem-Wrapper; Importservices dürfen das PyO3-Modul nicht direkt für
Mutationen ansprechen.

Ein versionierter Ergebnisdatentyp soll mindestens enthalten:

```python
@dataclass(frozen=True)
class ReportSourceSnapshot:
    contract_version: Literal["report_source_snapshot_v1"]
    staging_path: Path
    size_bytes: int
    modified_time_ns: int
    sha256: str
```

Die native Operation soll sinngemäß
`stable_snapshot_to_path(source, temporary_target, chunk_size)` heißen und:

- die Quelle genau einmal öffnen;
- symbolische Verknüpfungen und nicht reguläre Dateien ablehnen;
- Gerätekennung, Inode, Größe und nanosekundengenaue Änderungszeit vor und
  nach dem Lesen über den geöffneten Dateideskriptor vergleichen;
- zusätzlich prüfen, dass der Quellpfad nach dem Lesen noch dieselbe Datei
  bezeichnet;
- Kopieren und SHA-256-Bildung in einem Durchlauf durchführen;
- den Python Global Interpreter Lock während der blockierenden Arbeit
  freigeben;
- kurze Schreibvorgänge und vorzeitiges Dateiende als Fehler behandeln;
- Zieldatei und Zielverzeichnis vor Veröffentlichung synchronisieren;
- nur durch atomare Umbenennung innerhalb desselben Dateisystems
  veröffentlichen;
- bei jedem Fehler das unveröffentlichte temporäre Ziel entfernen;
- niemals selbst Datenbankzustand oder kanonische Anwendungszustände ändern.

Rust-Fehler werden am Python-Rand in konkrete, typisierte Fehler übersetzt.
Breite `RuntimeError`- oder Stringauswertung ist nicht der dauerhafte Vertrag.

## Sperren, Lease und Fencing

Eine Sperrdatei mit rein altersbasierter Rücknahme ist für lange klinische Jobs
nicht ausreichend. Die Zielimplementierung trennt:

- lokalen Dateianspruch über eine betriebssystemverwaltete advisory lock, zum
  Beispiel `flock`, deren Besitz beim Prozesstod endet;
- clusterweite Inhaltskoordination über Datenbankzustand oder eine
  lease-basierte Sperre;
- Fencing über einen monotonen Versuchstoken.

Eine Lease enthält mindestens Besitzerkennung, Hostkennung, Versuchstoken,
Erstellungszeit, Ablaufzeit und Heartbeat. Personenbezogene Daten oder rohe
Pfade gehören nicht in den Lease-Datensatz.

Jede Finalisierung prüft atomar:

- der Versuch ist noch aktueller Besitzer;
- sein Fencing-Token entspricht dem Datensatz;
- das kanonische Ziel wurde vollständig validiert;
- kein neuerer Versuch wurde begonnen.

Eine Eindeutigkeitsbedingung auf der kanonischen Inhaltsidentität bleibt die
letzte Verteidigung gegen doppelte Datensätze. Eine
`IntegrityError`-Behandlung darf nur den erwarteten
Eindeutigkeitskonflikt behandeln und muss anschließend den Gewinner vollständig
validieren.

## Vertrag mit `lx-anonymizer`

Der Bibliotheksaufruf soll langfristig einen typisierten
`ReportAnonymizationRequest` erhalten:

- Vertragsversion;
- Pfad des unveränderlichen sensiblen Snapshots;
- erwarteter Quellhash und erwartete Größe;
- eindeutige Versuchkennung;
- ausschließlich diesem Versuch zugeordnetes Ausgabeverzeichnis;
- explizite Feature- und Provideroptionen;
- Abbruch- beziehungsweise Deadline-Information.

Das Ergebnis soll als versionierter `ReportAnonymizationResult` mindestens
liefern:

- ursprünglichen und anonymisierten Text;
- validierte sensitive Metadaten;
- Pfad des unveröffentlichten Ergebnisartefakts;
- Ergebnisgröße und SHA-256;
- verwendete Vertrags-, Paket-, Modell- und Regelversionen;
- deterministische Warnungen und Qualitätsinformationen.

Gemeinsame Datentypen gehören nach Möglichkeit in `lx_dtypes`. Beide
Repositories dürfen keine abweichenden untypisierten Dictionaries als
Parallelvertrag pflegen.

`endoreg-db` akzeptiert niemals einen von `lx-anonymizer` frei gewählten
kanonischen Zielpfad. Die Bibliothek schreibt nur in das übergebene
Versuchsverzeichnis; die kanonische Publikation bleibt beim Importservice.

## Python-Fallback und Produktionsprofil

Der Python-Fallback muss:

- explizit konfigurierbar sein;
- dieselben fachlichen Nachbedingungen wie die native Implementierung erfüllen;
- als strukturiertes Ereignis und Metrik sichtbar sein;
- in Paritätstests dieselben Hashes und Fehlerklassen erzeugen.

Im Produktionsprofil ist `report_source_snapshot_v1` eine verpflichtende
native Fähigkeit. Fehlt sie, schlägt die Readiness-Prüfung fehl. Ein
Entwicklungsprofil darf den Fallback verwenden, darf aber nicht behaupten,
native Parallelität zu testen.

Das native Modul stellt eine maschinenlesbare Capability-Funktion bereit,
beispielsweise:

```text
native_capabilities()
  -> [("report_source_snapshot_v1", contract_version, implementation_version)]
```

## Fehler- und Cleanup-Matrix

| Fehlerpunkt | Erforderliches Verhalten |
| --- | --- |
| Quelle ändert sich während Snapshot | Abbruch vor Datenbankmutation; temporären Snapshot entfernen |
| Prozess stirbt während Snapshot | Keine veröffentlichte Zieldatei; Reconciliation entfernt verwaistes temporäres Artefakt |
| Identischer Inhalt wird parallel importiert | Ein Gewinner; Verlierer validiert und verwendet das kanonische Ergebnis |
| `lx-anonymizer` bricht ab | Versuch als fehlgeschlagen protokollieren; kein kanonisches Ergebnis veröffentlichen |
| Ergebnis-PDF ist ungültig | Quarantäne oder Versuchscleanup gemäß Policy; niemals Erfolg markieren |
| Datenbankfehler nach Dateipublikation | Artefakt als nicht referenziert erkennen; Reconciliation ordnet zu oder quarantänisiert fail-closed |
| Worker verliert Lease | Alle weiteren Zustandsänderungen und Publikation verweigern |
| Native Fähigkeit fehlt | Produktionsstart verweigern; nur explizites Entwicklungsprofil darf fallbacken |

Cleanup arbeitet nur in einem aufgelösten, erlaubten Wurzelverzeichnis und
entfernt nie das einzige validierte kanonische Ergebnis.

## Parallelität und Ressourcenbegrenzung

- Dateikopien und Hashing laufen nativ ohne Python Global Interpreter Lock.
- Datenbanktransaktionen bleiben kurz und umfassen keine optische
  Zeichenerkennung, Sprachmodelle oder vollständige Dateikopien.
- Die Zahl paralleler Anonymisierungen ist pro Host begrenzt.
- Optische Zeichenerkennung, native Bibliotheken und Machine-Learning-Runtimes
  erhalten explizite Threadbudgets, damit mehrere Worker den Host nicht durch
  verschachtelte Threadpools überbelegen.
- Warteschlangen-Backpressure ist einem unbegrenzten lokalen Executor
  vorzuziehen.
- Abbruch beendet Unterprozesse und wartet auf deren Ende, bevor Lease oder
  Versuchsverzeichnis freigegeben werden.

## Strukturierte Beobachtbarkeit

Mindestens folgende Ereignisse sind erforderlich:

- `report_import.source_claimed`;
- `report_import.snapshot_started`;
- `report_import.snapshot_completed`;
- `report_import.snapshot_rejected`;
- `report_import.content_claim_waited`;
- `report_import.duplicate_reused`;
- `report_import.anonymizer_started`;
- `report_import.anonymizer_completed`;
- `report_import.publication_completed`;
- `report_import.fencing_rejected`;
- `report_import.cleanup_completed`;
- `report_import.native_fallback_used`.

Ereignisse enthalten Versuchkennung, gehashte oder abstrahierte Pfadreferenz,
Hashpräfix, Bytezahl, Dauer, Backend- und Vertragsversion sowie Ergebnisstatus.
Sie enthalten keine Patientennamen, extrahierten Texte, vollständigen
Rohbericht oder geheimen Schlüssel.

Metriken umfassen Wartezeiten, Snapshot-Durchsatz, parallele Versuche,
Deduplizierungsrate, Fencing-Abweisungen, Cleanup-Fehler,
Fallback-Verwendung und End-to-End-Latenz.

## Verifikation

### Rust

- Unit-Tests für leere, kleine und große Dateien sowie verschiedene
  Chunkgrößen;
- Austausch per atomarer Umbenennung während des Lesens;
- In-place-Änderung und Trunkierung während des Lesens;
- symbolische Verknüpfung, Verzeichnis, nicht lesbare Quelle und volles Ziel;
- Fehler vor und nach `fsync`;
- SHA-256-Parität zur Python-Referenz;
- Prüfung, dass kein temporäres Ziel zurückbleibt.

### Python und Datenbank

- Lock wird vor dem Snapshot erworben;
- Snapshot wird vor Datenbankmutation erstellt;
- zwei Prozesse, gleicher Pfad;
- zwei Prozesse, verschiedene Pfade mit identischem Inhalt;
- mindestens acht parallele unterschiedliche PDF-Importe;
- Prozessabbruch an jeder Publikationsgrenze;
- Lease-Ablauf und Ablehnung eines alten Fencing-Tokens;
- Retry nach Snapshot-, Anonymizer-, Publikations- und Datenbankfehler;
- genau ein kanonisches `RawPdfFile`, eine erfolgreiche History und ein
  validiertes Ausgabe-PDF;
- keine verwaisten Versuchsartefakte nach Reconciliation.

### Paket und Deployment

Continuous Integration muss aus einem sauberen Checkout:

1. Rust-Tests ausführen.
2. Stubs regenerieren und einen leeren Diff verlangen.
3. Wheel bauen und in eine frische Umgebung installieren.
4. Capability-Funktion und native Snapshot-Operation aus dem installierten
   Wheel aufrufen.
5. Python-Fallback-Parität separat testen.
6. Cross-Repository-Vertragstests gegen die älteste unterstützte und aktuelle
   `lx-anonymizer`-Version ausführen.

Ein lokal vorhandenes, nicht versioniertes Shared Object ist kein gültiger
Nachweis.

## Einführungsreihenfolge

1. Typen und Fehlerklassen in `lx_dtypes` beziehungsweise an der lokalen
   Integrationsgrenze festlegen.
2. Native Snapshot-Operation und Python-Parität implementieren.
3. Snapshot hinter dem typisierten Dateisystem-Wrapper integrieren.
4. Eindeutige Versuchsverzeichnisse und Ergebnisvalidierung einführen.
5. Advisory Lock, Lease und Fencing ergänzen.
6. `lx-anonymizer` auf den versionierten Request-/Result-Vertrag umstellen.
7. Multiprozess-, Crash- und Cross-Version-Tests aktivieren.
8. Capability-Readiness zunächst beobachtend, anschließend im
   Produktionsprofil verpflichtend schalten.

Jede Stufe muss importierbar bleiben, vorhandene Stream-Endpunkte unberührt
lassen und über einen sicheren Rollback auf den vorherigen vollständig
validierten Zustand verfügen.
