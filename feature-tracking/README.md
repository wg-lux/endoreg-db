# Feature-Tracking für Produktionsreife

Dieses Verzeichnis ist die verbindliche Übersicht über die Produktionsreife der
größeren `endoreg_db`-Features. Produktionsreife wird nicht frei vergeben. Sie
wird aus den Pflichtkriterien der jeweiligen YAML-Datei abgeleitet.

Ein Prozentwert zeigt den Fortschritt, ist aber keine Freigabe. Ein Feature ist
erst `PRODUKTIONSREIF`, wenn jedes Pflichtkriterium mit nachvollziehbarer
Evidenz als `verified` bewertet wurde.

## Schnellstart

Alle Befehle werden im Repository-Root ausgeführt:

```bash
./feature-tracking/tracker.py
./feature-tracking/tracker.py show dicom
./feature-tracking/tracker.py validate
./feature-tracking/tracker.py check dicom fhir
./feature-tracking/tracker.py overview --all
```

Ein strukturell und gemäß `policy.yml` gültiges Feature erscheint in der
argumentlosen Übersicht als `evaluiert`. Das bedeutet, dass seine Definition
erfolgreich ausgewertet wurde; der Score und die Kriterien zeigen weiterhin,
wie viel der Definition of Done tatsächlich verifiziert ist.

`check` liefert Exit-Code `1`, solange eines der ausgewählten Features nicht
produktionsreif ist. Damit kann derselbe Stand lokal und in CI geprüft werden.
Ungültiges YAML, eine verletzte Policy oder ein nicht sicher ausführbarer
Befehl liefert Exit-Code `2`.

## Commit-Gate

Die Pre-Commit-Konfiguration installiert zusätzlich einen `commit-msg`-Hook.
Sobald eine Commit-Message eine Feature-ID oder den ausgeschriebenen
Feature-Namen enthält, müssen alle Pflichtkriterien dieses Features im
gestagten YAML-Stand verifiziert sein. Andernfalls wird der Commit abgelehnt.

Beispiele für erkannte Referenzen sind `dicom`, `feat(dicom)`,
`audit_ledger`, `audit-ledger` und `Multi-Center-Hub-Ingest`. Groß- und
Kleinschreibung sowie `_`, `-` und Leerzeichen werden normalisiert. Kommentare
aus dem Commit-Template werden ignoriert.

Der Hook wird für bestehende Checkouts einmalig installiert:

```bash
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Die Prüfung kann direkt reproduziert werden:

```bash
./feature-tracking/tracker.py guard-commit-message .git/COMMIT_EDITMSG
```

Der Guard liest Feature-YAML und Policy aus dem Git-Index. Eine nur im
Arbeitsbaum geänderte Bewertung kann die Prüfung daher nicht umgehen.

## Bewertungen aktualisieren

Eine manuelle Prüfung wird mit Prüfer und Evidenz dokumentiert:

```bash
./feature-tracking/tracker.py update dicom security_controls \
  --status verified \
  --assessed-by "name@example.org" \
  --evidence review "security-review-2026-07-17" \
  --evidence test "tests/services/test_dicom_interoperability.py"
```

Ein Blocker muss begründet werden:

```bash
./feature-tracking/tracker.py update dicom operational_readiness \
  --status blocked \
  --assessed-by "name@example.org" \
  --note "Wiederherstellungsübung noch nicht durchgeführt"
```

Eine Bewertung kann vollständig zurückgesetzt werden:

```bash
./feature-tracking/tracker.py update dicom operational_readiness \
  --status not_assessed
```

Automatisierte Kriterien können direkt ausgeführt werden. Ohne `--update`
bleibt YAML unverändert:

```bash
./feature-tracking/tracker.py verify dicom
./feature-tracking/tracker.py verify dicom automated_tests \
  --update --assessed-by "name@example.org"
```

Bei `verify --update` wird ein erfolgreiches Kommando als `verified`, ein
fehlgeschlagenes Kommando als `blocked` gespeichert. Updates verwenden die
atomaren und strukturiert protokollierten Dateioperationen des Projekts.

## Tracking abschließen

Ein Feature kann erst dann als `done` aus dem aktiven Tracking entfernt werden,
wenn alle Pflichtkriterien verifiziert sind:

```bash
./feature-tracking/tracker.py done dicom \
  --assessed-by "name@example.org" \
  --note "Produktionsfreigabe 2026-07"
```

`done` wird mit Zeit, Person und Begründung in der YAML-Historie gespeichert.
Das Feature verschwindet aus der argumentlosen Übersicht, dem ungezielten
`check` und dem Commit-Message-Gate. Es bleibt über `show` und
`overview --all` sichtbar.

Neue Anforderungen können das Feature kontrolliert wieder öffnen:

```bash
./feature-tracking/tracker.py reopen dicom documented_scope \
  --assessed-by "name@example.org" \
  --note "Neues DICOM-Profil wird unterstützt"
```

Das beim Reopen benannte Kriterium wird auf `in_progress` gesetzt. Dadurch kann
ein wieder geöffnetes Feature nicht fälschlich produktionsreif bleiben.
Bewertungen eines abgeschlossenen Features sind ansonsten unveränderlich.

## Definition of Done

Jede Feature-Datei enthält:

- eine stabile `id`, einen verständlichen Namen, Beschreibung und Owner;
- mindestens die in `policy.yml` festgelegten Pflichtkategorien;
- konkrete, überprüfbare Aussagen unter `acceptance`;
- genau eine manuelle oder automatisierte Verifikationsmethode je Kriterium;
- eine Bewertung mit Zeit, Prüfer und Evidenz, sobald sie nicht mehr
  `not_assessed` ist.

Die erlaubten Bewertungsstände sind:

- `not_assessed`: noch keine belastbare Bewertung;
- `in_progress`: Umsetzung oder Verifikation läuft;
- `blocked`: ein benannter Blocker verhindert die Erfüllung;
- `verified`: Akzeptanzkriterien sind erfüllt und Evidenz ist hinterlegt.

Für klinisch oder sicherheitstechnisch relevante Kriterien gibt es keine
implizite Freistellung. Wenn ein Kriterium tatsächlich nicht verpflichtend ist,
muss es im Review ausdrücklich mit `required: false` modelliert werden; es
trägt dann nicht zur Produktionsfreigabe bei.

## Evidenz

Evidenz ist eine stabile, für Reviewer auffindbare Referenz. Geeignet sind
Testdateien oder Testkommandos, Review-IDs, Runbooks, Monitoring-Dashboards,
freigegebene Dokumente und reproduzierbare Demonstrationen. Aussagen wie
„funktioniert lokal“ oder ein Prozentwert ohne Nachweis reichen nicht aus.

Automatische Kommandos sind als Argumentliste gespeichert und werden ohne
Shell ausgeführt. Dadurch können YAML-Inhalte keine Shell-Expansion oder
Pipelines einschleusen.

Repositoryübergreifende Kriterien verwenden mehrere kleine, geordnete
Kommandos. Jedes Kommando nennt sein absolutes Arbeitsverzeichnis; fehlt es,
wird wie bisher der Root von `endoreg_db` verwendet. Alle Kommandos werden
weiterhin direkt und ohne Shell ausgeführt, brechen beim ersten Fehler ab und
erzeugen bei `verify --update` getrennte Evidenz:

```yaml
verification:
  kind: command
  commands:
    - working_directory: /home/admin/endoreg-db
      command:
        - .devenv/state/venv/bin/pytest
        - tests/api/test_contract.py
        - -q
    - working_directory: /home/admin/dev/lx-annotate/frontend
      command:
        - npm
        - run
        - test:unit
        - --
        - src/api/__tests__/contract.test.ts
        - --run
  timeout_seconds: 600
```

Ein einzelnes Kommando darf weiterhin direkt unter `command` stehen. Ein
Kriterium darf entweder `command` oder `commands` verwenden, nie beides.

## Pflegeprozess

1. Akzeptanzkriterien vor oder mit der Implementierung schärfen.
2. Umsetzung und Tests durchführen.
3. Das passende Prüfkommando ausführen oder die manuelle Prüfung abschließen.
4. Bewertung samt belastbarer Evidenz aktualisieren.
5. `validate` und anschließend `check <feature>` ausführen.
6. Änderungen an Definition, Bewertung und Evidenz gemeinsam reviewen.

Die YAML-Dateien werden normal versioniert. Damit bleiben Änderungen an Scope,
Qualitätsmaßstab und Produktionsfreigabe im Git-Verlauf nachvollziehbar.

## Migrierte Markdown-Tracker

`policy.yml` inventarisiert frühere Markdown-Pläne und -TODOs. Jede dieser
Dateien muss über `source_documents` genau einem Feature mit
`disposition: migrated` zugeordnet sein. `validate` schlägt fehl, wenn eine
Zuordnung fehlt, doppelt ist oder auf eine nicht vorhandene Datei zeigt.

Die alten Dokumente bleiben als Designhistorie oder fachlicher Kontext
erhalten. Verbindliche Kriterien, Bewertung und Evidenz werden jedoch nur in
den Feature-YAML-Dateien gepflegt. Neue parallele Markdown-Tracker sind gemäß
`AGENTS.md` nicht zulässig.
