# Inkrementelle Reidentifikation nach Schlüsselkompromittierung

Das Experiment verwendet ausschließlich synthetische Personen und eigens erzeugte,
absichtlich offengelegte Testschlüssel. Produktionscode und Identitätspolitik werden
nicht verändert. Zuständige Kriterien stehen in
[Anonymization.yml](../../feature-tracking/Anonymization.yml),
`incremental_reidentification_experiment`.

## Ausführen

Vom Repository-Root, unter Linux x86_64 mit Landlock-Unterstützung:

```sh
.devenv/state/venv/bin/pyright scripts/reidentification
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .devenv/state/venv/bin/pytest -c scripts/reidentification/pytest.ini --confcutdir=scripts/reidentification scripts/reidentification/test_harness.py -q
env -i PATH="$PATH" .devenv/state/venv/bin/python -m scripts.reidentification.run --runtime /tmp/endoreg-identity-audit-new
```

`run.py` führt nacheinander die Produktions-Konformitätsprüfung, den Generator,
jeweils einen neuen isolierten Baseline-/Angreiferprozess und anschließend den
privaten Scorer aus. `generate`, `execute` und `score` sind getrennte Funktionen;
der Angreifer importiert ausschließlich `worker.py`, niemals den Generator.
Die Prüfung initialisiert Django ohne installierte Anwendungen und mit leerer
Datenbankkonfiguration. Vor Helper-Aufrufen wird der Keyring-Snapshot auf `None`
gesetzt. Reale Settings, Salt-Dateien und klinische Datenbanken werden nicht geladen.

Die Ausgabe liegt unter `<runtime>/export/reidentification/`:

- `public/`: Quellrevision, Prüfsummen, Konformitätsvektoren, Wissen je Szenario,
  Baseline- und Angreiferausgaben sowie `scores.json` und `scores.csv`.
- `private/`: reproduzierbare Seeds, Testschlüssel, Ground Truth und vollständige
  Angreiferbündel. Verzeichnisse haben Modus 0700, Dateien 0600. Nicht veröffentlichen.

Das vollständige Angreiferbündel enthält nur die deklarierten Eingaben; seine
Ablage unter `private/` schützt das absichtlich enthaltene Testschlüsselmaterial.
Der Worker bekommt den Inhalt über stdin, keinen Zugriff auf dieses Verzeichnis.
Ein einzelner Worker lässt sich mit einem solchen Bündel erneut ausführen:

```sh
env -i .devenv/state/venv/bin/python -I scripts/reidentification/worker.py < /tmp/endoreg-identity-audit-new/export/reidentification/private/0-B_shared-attacker.json
```

Der Oracle-Fall benötigt den Supervisor `execute`, der höchstens 32 Anfragen
beantwortet. Er ist eine synthetische API-Modellierung, keine aktive zentrale API.
Der private Scorer kann abgeschlossene Ergebnisse ohne erneuten Angriff überprüfen:

```sh
env -i PATH="$PATH" .devenv/state/venv/bin/python -m scripts.reidentification.rescore --runtime /tmp/endoreg-identity-audit-new
```

Exakte Wiederholung mit unveränderten Kandidaten und neuen Ausgabeordnern:

```sh
env -i PATH="$PATH" .devenv/state/venv/bin/python -m scripts.reidentification.run --runtime /tmp/endoreg-identity-audit-replay --replay /tmp/endoreg-identity-audit-new/export/reidentification/private/reproduction.json
```

Seeds und Schlüssel bleiben gleich, Laufzeiten und Speicherwerte können abweichen.
Eine geänderte Konfiguration wird beim Replay abgewiesen. Vor einem historischen
Replay müssen zusätzlich die Quellprüfsummen aus `revision.json` übereinstimmen.
Die private Reproduktionsdatei ist kein Angreifereingang.

## Isolation und Messgrenzen

Der Supervisor startet einen neuen Interpreter mit `-I`, leerer Umgebung bis auf
`PYTHONHASHSEED`, cwd `/` und geschlossenen zusätzlichen Dateideskriptoren. Nach dem
Laden des festen Programms und seiner Bibliotheken, aber vor dem Lesen des Bündels,
verweigert Landlock sämtliche neuen Datei-/Verzeichnislesezugriffe und Ausführungen.
Vier reale Leseproben müssen mit `PermissionError` scheitern. Ohne Landlock bricht
der Lauf ab; es gibt keinen stillen Wechsel zu bloßer logischer Trennung.

stdin/stdout/stderr bleiben als Pipes verfügbar. Netzwerk und Metadatenabfragen
werden durch diese Landlock-Regeln nicht allgemein gesperrt; der feste Worker
enthält keine Netzwerkzugriffe. Bereits geladener Code ist vertrauenswürdig.
Der Schutz adressiert versehentliche Benchmark-Leaks, nicht einen bösartigen
Administrator, der Programme oder den Host kontrolliert.

Jeder Fall hat höchstens 250.000 Kandidat-Kontext-Auswertungen bzw. 60 Sekunden,
je nachdem, was zuerst erreicht wird. Dazu kommen 512 MiB Adressraumlimit,
65 CPU-Sekunden und 70 Sekunden Supervisor-Wartegrenze. Der Cursor zählt tatsächlich
abgeschlossene Auswertungen in der eingefrorenen Reihenfolge. Alle Treffer werden
behalten. Der breite Datumsraum ist einschließlich beider Grenzen zu verstehen.
`unique_tuples` bezeichnet das vollständige Kandidatenuniversum; `reached_tuples`
zählt tatsächlich ausgewertete Tupel. Mehrere Zielzeilen teilen dieselbe Berechnung.

Die erfasste Laufzeit umfasst den Worker-Suchlauf, nicht Python-/Django-Start,
Dateiübertragung oder die Produktions-Keyvalidierung pro Versuch. `max_rss_kib`
enthält dagegen den Interpreter-Höchststand. Oracle-Primitive werden serverseitig
berechnet; bei Patienten entspricht eine beantwortete Anfrage einem HMAC. Die
Worker-Spalte `primitive_hmacs` bleibt dort folgerichtig null.

## Auswertung lesen

Vier Abbildungen aus den unveränderten öffentlichen Messdaten erzeugen:

```sh
env -i PATH="$PATH" LD_LIBRARY_PATH="$LD_LIBRARY_PATH" .devenv/state/venv/bin/python -m scripts.reidentification.plot --runtime /tmp/endoreg-identity-audit-figures
```

PNG/SVG und das gemeinsame vierseitige PDF liegen unter
`<runtime>/export/reidentification/figures/`. Gezeigt werden zusätzliche korrekte
und falsche Zuordnungen, Mitgliedschaft einschließlich Abstentionen, Zielabdeckung
und gemessene Kosten. Es werden weder neue Angriffe ausgeführt noch private
Ground-Truth-Dateien gelesen. Alle Nenner und synthetischen Grenzen sind beschriftet.

Die Hauptfrage ist der Unterschied zwischen gleichen Eingaben ohne und mit
relevantem Schlüssel. Baseline-Lookups propagieren bekannte Identität-Token-Paare
über gleiche Digests; explizite Zeilenlabels bleiben maßgeblich. Widersprüchliche
Kandidaten bleiben mehrdeutig. Der Scorer vergibt keine Personenidentifikation,
indem er ein gefundenes Tupel nachträglich mit einer privaten Personen-ID verknüpft.

Eine eindeutige Person im Hilfskorpus ist nur eine bedingte Personenbehauptung:
Eine unbekannte weitere Person kann dasselbe kodierte Tupel besitzen. Der gezielt
eingebaute abwesende Zwilling wird deshalb als Falschzuordnung gewertet. Bei
bekannten Mehrfachkandidaten wird enthalten/mehrdeutig getrennt ausgewiesen.
Kein Fall behauptet Abwesenheit. Nullwerte bei echten/falschen Negativen sind
Abstentionen, keine bewiesene Nichtmitgliedschaft. Der Einzelziel-Fall erlaubt nur
Aussagen über den exponierten Ausschnitt, nicht über die gesamte Datenbank.

`coverage` trennt den vollständigen Kandidatenraum, den erreichten Präfix,
Schlüssel-/Kontext-/Schema-Inkompatibilität und erreichte Nichttreffer. Für die
Nenner gelten: alle Zeilen = `eligible_rows`; im kompatiblen Kandidatenraum =
`reached_matched + inside_beyond_prefix + reached_no_match`; erreicht =
`reached_matched + reached_no_match`. Ein negativer begrenzter Versuch belegt keine
Abwesenheit außerhalb dieses Bereichs.

Weitere Einordnung und gemessene Ergebnisse stehen in [REPORT.md](REPORT.md).
# Aktuelle UUID-Abbildung

Mit `--uuid-mapping` führt der bestehende Runner ausschließlich die Hash-Kontrollen
und die aktuelle lokale beziehungsweise Hub-UUIDv8-Abbildung aus. Derselbe Schalter
ist beim Replay erforderlich. Beispiel:

```sh
env -i PATH="$PATH" .devenv/state/venv/bin/python -m scripts.reidentification.run --uuid-mapping --runtime /tmp/uuid-audit-new
env -i PATH="$PATH" .devenv/state/venv/bin/python -m scripts.reidentification.run --uuid-mapping --runtime /tmp/uuid-audit-replay --replay /tmp/uuid-audit-new/export/reidentification/private/reproduction.json
env -i PATH="$PATH" .devenv/state/venv/bin/python -m scripts.reidentification.rescore --runtime /tmp/uuid-audit-new
```

Die Zielwerte stammen aus Produktionshelfern, die Angreiferberechnung ist unabhängig.
Der Kandidatenkorpus und die Budgets bleiben unverändert. Ergebnisse dieses Versuchs
stehen separat unter `evidence/uuid_mapping/`; die historischen Messungen bleiben erhalten.
