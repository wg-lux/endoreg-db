# Kandidatensuche mit bekanntem Salt

Das Skript testet mögliche Eingaben und misst die Laufzeit. Es invertiert SHA-256
nicht mathematisch. Erfolg setzt voraus, dass die gesuchte Eingabe im gewählten
Suchraum liegt. Zuständige Feature-Definition: [Anonymization.yml](../feature-tracking/Anonymization.yml),
Kriterium `sensitive_metadata_resolution`.

Die [referenzierte Revision](https://github.com/wg-lux/endoreg-db/blob/08c1ac0da110343dfc6d0314b24998a1937d9aeb/endoreg_db/utils/hashs.py#L53-L54)
verwendet **HMAC-SHA-256**, wobei der Salt als Schlüssel dient. Der Payload ist
kompaktes UTF-8-JSON: `["patient-identity-v1",first_name,last_name,dob,center_id]`.
`dob` ist ein ISO-Datum, `center_id` dort eine JSON-Zahl. Die aktuelle Implementierung
verwendet dagegen einen Zentrumsschlüssel als Zeichenkette und bereinigt Leerzeichen.
Exakte Serialisierung und die richtige Revision sind entscheidend.

## Beispiele

Selbsttest mit frei gewähltem Testschlüssel und vierstelligen Zahlen:

```sh
python scripts/benchmark_sha256.py --salt benchmark-only --plaintext 9876 \
  --min-length 4 --max-length 4 --max-seconds 30
```

Bekannten Zielhash durchsuchen (`TARGET_HASH` und `KNOWN_SALT` selbst setzen):

```sh
python scripts/benchmark_sha256.py --target "$TARGET_HASH" --salt "$KNOWN_SALT" \
  --charset abcdef0123456789 --min-length 1 --max-length 8 \
  --max-seconds 60 --max-attempts 5000000
```

Die historische JSON-Struktur lässt sich mit Präfix/Suffix nachbilden. Dieses
reproduzierbare Beispiel sucht ausschließlich die vier Ziffern des Geburtsjahres:

```sh
python scripts/benchmark_sha256.py --salt benchmark-only --plaintext 1980 \
  --prefix '["patient-identity-v1","Synthetic","Person","' \
  --suffix='-01-02",1]' --min-length 4 --max-length 4
```

Weitere Modi: `--algorithm sha256 --salt ''`, `--algorithm salt-prefix`
für `SHA256(salt + payload)` und `--algorithm salt-suffix` für
`SHA256(payload + salt)`. `--help` zeigt alle Parameter.

## Interpretation

Die JSON-Ausgabe enthält Status, gefundenen variablen Kandidaten, Versuche,
Suchraumgröße, Sekunden, Kandidaten pro Sekunde und eine extrapolierte Dauer
für den gesamten Suchraum. Diese Schätzung ist keine gemessene vollständige
Suche; sie hängt von Rechner, Algorithmus, Eingabelänge und Python-Overhead ab.
Bei wenigen Versuchen ist sie besonders ungenau. Bei gleichverteilter Position
eines vorhandenen Treffers liegt die durchschnittliche Versuchsanzahl ungefähr
bei der Hälfte des Suchraums. Für unbekannte beliebige 256-Bit-Preimages ist
dies keine praktikable Umkehrmethode.

Die Suche läuft sequenziell und speichert keine Kandidatenliste. Reihenfolge:
aufsteigende Länge, dann Reihenfolge der Zeichen in `--charset`. Länge 0 erlaubt
die leere Eingabe. Limits sind immer positiv; die Zeitgrenze wird zwischen
Kandidaten geprüft. Status: `found`, `exhausted`, `attempt_limit`, `time_limit`
oder `interrupted`. Exit-Code 0 bedeutet Treffer, 1 keinen Treffer, 2 einen
Parameterfehler. Nur `exhausted` schließt den gesamten angegebenen Suchraum aus.

Es gibt keine Datenbank-, Netzwerk- oder Dateizugriffe und keine Änderungen an
Produktions-Hashfunktionen. Das Skript bildet bewusst historische Algorithmen
isoliert nach; es erzeugt keine persistierten Anwendungsidentitäten. Salt und
Payload werden nicht automatisch aus Django geladen. Kommandozeilenargumente
können in Prozesslisten/Shell-History erscheinen; die Ausgabe enthält den Treffer.
Für Messungen eignen sich die oben explizit erzeugten Testwerte.
