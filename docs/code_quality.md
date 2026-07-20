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

Eine statische Meldung allein autorisiert keine Löschung. Vor einer Entfernung
sind Leaf- und Barrel-Imports, String-Imports, Django-Registrierungen, URLs,
Signale, Jobs, Commands, Paketexporte und repositoryübergreifende Verbraucher
zu prüfen. Öffentliche Verträge benötigen zuerst einen Deprecation-Pfad.

## Qualitäts-Boundary-Guard

`devenv tasks run quality:boundaries` friert den überprüften Altbestand an
breiten `Exception`-/`BaseException`-Handlern und Typunterdrückungen ein. Die
versionierte Baseline steht in `quality/quality_boundary_baseline.yml`. Ihr
Fingerprint basiert auf Datei, qualifiziertem Scope und Regel, nicht auf
Zeilennummern. Reine Zeilenverschiebungen bleiben deshalb stabil; neue,
entfernte oder verschobene Befunde verlangen einen bewussten Review.

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
