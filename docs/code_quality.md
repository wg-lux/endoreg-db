# Codequalität und Wartbarkeitsgrenzen

## Dead-Code-Verfahren

`devenv tasks run quality:dead-code` führt Vulture mit mindestens 90 Prozent
Konfidenz gegen `endoreg_db` und `scripts` aus. Migrationen, Tests,
Settingsmodule, Management Commands sowie Django-Registrierungsmodule werden
aus dem automatischen Löschpfad ausgeschlossen, weil sie häufig dynamisch
referenziert werden.

Die geprüften Ausnahmen stehen in `quality/dead_code_baseline.yml`. Jeder
Eintrag enthält Fundstelle, Toolmeldung, Konfidenz, Klassifikation, Begründung,
Owner und Reviewdatum. Der Guard schlägt fehl, wenn:

- ein neuer Fund erscheint,
- ein Baseline-Eintrag stale wird oder
- das Reviewdatum einer Ausnahme abläuft.

Dieselbe Datei enthält unter `deletion_candidates` separat untersuchte
Löschkandidaten. Sie besitzen Pfad und Zeilenbereich, Klassifikation, konkrete
Consumer-Evidenz, Risiko, empfohlene Aktion, Owner und Reviewdatum. Diese Liste
ist keine Vulture-Allowlist: `confirmed_dead` ist eine geplante Entfernung,
während `compatibility_contract` und `uncertain` vor einer Löschung weitere
Consumer-Evidenz verlangen.

Eine statische Meldung allein autorisiert keine Löschung. Vor einer Entfernung
sind Leaf- und Barrel-Imports, String-Imports, Django-Registrierungen, URLs,
Signale, Jobs, Commands, Paketexporte und repositoryübergreifende Verbraucher
zu prüfen. Öffentliche Verträge benötigen zuerst einen Deprecation-Pfad.

## Qualitäts-Boundary-Guard

`devenv tasks run quality:boundaries` friert den überprüften Altbestand an
breiten `Exception`-/`BaseException`-Handlern, parameterlosen `except:`-Klauseln
und Typunterdrückungen ein. Die versionierte Baseline steht in
`quality/quality_boundary_baseline.yml`. Ihr Fingerprint basiert auf Datei,
qualifiziertem Scope und Regel, nicht auf Zeilennummern. Reine
Zeilenverschiebungen bleiben deshalb stabil; neue, entfernte oder verschobene
Befunde verlangen einen bewussten Review. Eine parameterlose `except:`-Klausel
gilt dabei als breiter Handler und benötigt denselben benannten Boundary-Review.

Die Baseline ist kein Allowlisting und keine Aussage, dass der Altbestand
zulässig ist. Eine Aktualisierung ist nur Teil einer benannten Kohorte:

1. Breite Handler als echte HTTP-, Command-, Job-, Storage- oder
   Integrationsboundary belegen oder durch konkrete Exception-Typen ersetzen.
2. Type-Ignores bevorzugt durch einen engeren Framework-Adapter oder eine
   korrekte Annotation entfernen. Ein notwendiger Ignore braucht Begründung,
   Owner und Abbaupfad.
3. Count und Fingerprint erst nach Review anpassen und das Reviewdatum nicht
   ohne erneute Prüfung verlängern.
4. Pyright, den Boundary-Guard und die fokussierten Fehlerpfadtests ausführen.

## Reproduzierbarer Qualitätslauf

`devenv tasks run quality:code-regression` führt Pyright, Dead-Code-Guard,
Boundary-Guard und die schnelle Pytest-Marker-Lane in der bereits
synchronisierten Projekt-Venv aus. Vor dem Lauf sind Abhängigkeiten bei Bedarf
einmalig mit `devenv tasks run agent:sync` zu aktualisieren. Der Qualitätslauf
startet absichtlich keinen verschachtelten `test:sync`: eine erneute vollständige
Auflösung innerhalb desselben Task-Zeitfensters kann den Testprozess trotz
grüner Tests abbrechen.

`devenv tasks run test:fast` bleibt der eigenständige Entwickler- und PR-Lauf;
er synchronisiert seine Testabhängigkeiten weiterhin selbst und verwendet
dieselben Marker, Umgebungsgrenzen, Parallelisierung und Datenbank-Wiederverwendung
wie der Qualitätslauf.

## Helper- und Klassengrenzen

- Reine Transformationen sind typisierte, seiteneffektfreie Funktionen.
- Datenbank-, Netzwerk- und Dateisystemoperationen liegen in Services oder
  expliziten Integrationsgrenzen.
- Django-Modelle besitzen Persistenz, Constraints und dünne Zustandsübergänge,
  aber keine Workflow-Orchestrierung oder Service-Rückimporte.
- Helpermodule haben einen fachlichen Owner und dürfen nicht zu allgemeinen
  Sammelmodulen oder konkurrierenden Implementierungen anwachsen.
- Neue Barrel-Imports und Importzyklen sind nicht zulässig.

## Exception-Grenzen

Domain- und Servicecode soll kleine typisierte Fehlerhierarchien verwenden.
HTTP-, Command-, Job- und Integrationsgrenzen übersetzen diese Fehler zentral
in Status, Exit-Code, Retry-Klassifikation und strukturierte Logs. Innere
Funktionen fangen keine breiten Exceptions und unterdrücken keine Fehler.

Security-, Storage-, Kryptografie- und klinische Invarianten bleiben
fail-closed. Fehlerantworten und Logs dürfen keine Secrets, Master Keys,
direkten Patientenidentifikatoren oder vollständigen Payloads enthalten.

### Konfigurationsgrenze

Die zentralen Parser in `endoreg_db.config.env` verwenden einen Default nur,
wenn eine Variable nicht gesetzt ist. Ein gesetzter, aber syntaktisch
ungültiger Wert löst `EnvironmentValueError` aus und verhindert damit einen
Start mit unklarer oder abgeschwächter Konfiguration. Boolesche Werte
akzeptieren ausschließlich `1`/`0`, `true`/`false`, `yes`/`no` und `on`/`off`,
jeweils unabhängig von Groß- und Kleinschreibung. Integer müssen durch
`int()` parsebar sein; sicherheits- oder betriebsrelevante Aufrufer erzwingen
zusätzlich ihre explizite Untergrenze. Fließkommazahlen müssen endlich sein. Geschlossene
Moduswerte werden über `env_choice` normalisiert und gegen eine typisierte
Wertemenge geprüft. Unbekannte oder leere Modi dürfen weder protokolliert noch
stillschweigend durch `celery`, `inline` oder einen anderen Default ersetzt
werden.

Python lädt eine `.env`-Datei ausschließlich für die eigenen
`endoreg_db.config.settings.dev`- und `case_gen`-Profile und immer mit
`override=False`. Test-, Produktions- und eingebettete Consumer-Settings laden
keine Datei durch einen Bibliotheksimport; Devenv, Secretspec oder der
Prozess-Supervisor müssen dort die Umgebung vor dem Python-Start bereitstellen.
Bei Pfadvariablen ist eine explizit leere oder nur aus Leerzeichen bestehende
Belegung ungültig und löst `EnvironmentValueError` aus. Nur eine vollständig
fehlende Variable darf den dokumentierten Default verwenden. Insbesondere darf
ein leerer geschützter Runtime-Root weder zum Repository-Root noch zu einem
aus dem Text `None` abgeleiteten Pfad werden.
Der Debug-Snapshot redigiert Broker-Adressen, Datenbanknamen, Storage- und
Staging-Pfade sowie den Repository-Pfad. `DOTENV_LOADED` beschreibt nur einen
tatsächlich erfolgreichen Development-Load und nicht bloß den Import des
Pakets.

Die Exception nennt nur Variablenname und erwarteten Typ. Der konfigurierte
Rohwert wird weder in die Meldung noch in Logs übernommen. Deployment- und
Consumer-Manifeste sind vor dem Rollout gegen die akzeptierten Schreibweisen
zu prüfen. Abbruchkriterium ist jeder unerwartete Startup-Fehler mit einer
bisher dokumentierten Schreibweise; Rollback ist die Rücknahme dieser
Parserkohorte, nicht ein stiller Fallback auf einen Default.

Die erste migrierte Kohorte ist DICOM/FHIR-Interoperabilität:

- `endoreg_db.exceptions` besitzt die fachlichen Fehlercodes, sicheren Texte,
  Auditgründe und Retry-Klassifikation.
- Services wandeln erwartete Validierungs- und Integritätsfehler in diese
  Typen um und erhalten die interne Ursache per Exception-Chaining.
- `endoreg_db.views.interoperability_errors` besitzt ausschließlich die
  HTTP-Übersetzung. Interne Fehlermeldungen gelangen nicht in die Antwort.
- Unbekannte Fehler werden am Integrationsrand strukturiert protokolliert und
  unverändert weitergereicht. Sie werden nicht zu 4xx-Fehlern umgedeutet.

Die Kohorte ändert das öffentliche FHIR-Fehlerformat für erwartete, ungültige
Exportdaten auf HTTP 422 mit `code`, `detail` und `retryable`. Rollback erfolgt
durch Zurücknahme des View-Mappers; Abbruchkriterium ist eine unerwartete
Umklassifizierung interner Fehler oder die Offenlegung interner Detailtexte.

Die zweite migrierte Kohorte ergänzt Command- und Job-Grenzen:

- `backfill_dicom_manifest_v2` übersetzt den typisierten Backfill-Fehler über
  `endoreg_db.management.command_errors` in Exitcode 1 sowie einen stabilen,
  datensparsamen Code und Text. Interne Datensatzdetails bleiben ausschließlich
  in der verketteten Serviceursache.
- `MediaOperationDeferred` besitzt zentral eine Retry-Klassifikation. Die drei
  betroffenen Celery-Tasks verwenden dieselbe Policy mit mindestens 60 Sekunden
  Verzögerung und höchstens 20 Versuchen und protokollieren nur Jobname,
  gehashte Objektidentität, Fehlercode und Retry-Parameter.
- Andere oder unbekannte Jobfehler werden nicht automatisch erneut versucht
  und unverändert an Celery weitergereicht.

Die öffentliche Importposition der beiden verschobenen Exceptions bleibt als
Kompatibilitätsvertrag erhalten. Rollback ist die Rücknahme der beiden
Boundary-Adapter; Abbruchkriterium sind erhöhte Retry-Raten, unbegrenzte
Wiederholungen oder interne Detailtexte in Command-Ausgabe beziehungsweise
Job-Logs.

Für jede weitere Kohorte werden vor Deployment Owner, betroffene öffentliche
Verträge, Metriken/Logs, Abbruchkriterium und ein reversibler Rollback benannt.
Baseline-Reduktionen bleiben beim Rollback erhalten, sofern kein entfernter
Kompatibilitätsvertrag wiederhergestellt werden muss; eine Erhöhung benötigt
erneut einen expliziten Review.

## Review-Checkliste

1. Sind statische und dynamische Verbraucher geprüft?
2. Ist der Code am fachlich zuständigen Layer angesiedelt?
3. Sind Ein- und Ausgaben konkret typisiert?
4. Werden Seiteneffekte ausschließlich an einer sichtbaren Boundary ausgeführt?
5. Wird eine konkrete Exception erzeugt und nur an der zuständigen Boundary übersetzt?
6. Bleiben Fehlerursache, Chaining, Auditierbarkeit und Fail-closed-Verhalten erhalten?
7. Decken Pyright, Import-Boundary-, Runtime- und Fehlerpfadtests die Änderung ab?
