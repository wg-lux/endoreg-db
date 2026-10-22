# Messergebnis: zusätzlicher Identitätsgewinn nach HMAC-Schlüsselverlust

Stand: 21.10.2026. Untersucht wurde `prototype`, Commit
`a965c67454013a92a95470e533a7f38a40ef4ddd`, mit bereits vorhandenem Dirty State.
Die unveränderten Produktionshelfer und ihre geladenen Anwendungsabhängigkeiten
sind in [revision.json](evidence/revision.json) gehasht. Vorhandene Änderungen an
Patient Matching wurden nicht bearbeitet. Eine separate beigefügte `hashs.py`
war nicht verfügbar; verglichen wurden der Checkout und die Formeln der Aufgabe.

**Unter den ausdrücklich modellierten Eingaben brachte der kompromittierte
gemeinsame Schlüssel mit unabhängiger Kandidatenpopulation 144 zusätzliche
korrekte Zeilen-Person-Zuordnungen und 36 korrekte Mitgliedschaftsbehauptungen.
Es entstanden zugleich 12 falsche Zeilenzuordnungen und drei falsche
Mitgliedschaftsbehauptungen.** Diese Zahlen summieren drei synthetische Versuche;
sie sind keine Schätzung für eine klinische Bevölkerung.

Ohne Namen/Jahreshinweise wurden kodierte Identitätsattribute gefunden, aber keine
Personenidentifikation nachträglich durch den privaten Scorer erfunden. Bereits
beschriftete Zeilen ergaben exakt null zusätzliche Zuordnungen. Ein Schlüssel ohne
Hash-Datensatz ergab keine Vergleichsbeobachtung.

## Eingaben, Trennung und Vorwissen

Die eingefrorene [Konfiguration](config.yml) enthält zwölf synthetische Namenspaare
(elf nach `strip`), bekannte/unübliche Schreibweisen, Mehrfachnamen, Anführungszeichen,
Backslash, Unicode-Kompositionsvarianten und Rand-Whitespace. Die Reihenfolge ist
handgeschrieben, keine gemessene Namenshäufigkeit. Geburtsdaten werden im breiten
Modell vom **01.01.1900 bis 21.10.2026 einschließlich: 46.315 Tage** durchsucht.
Das sind 509.465 Namens-Datum-Tupel und bei zwei Zentren 1.018.930 Auswertungen.
Die Grenze 250.000 wird vorher festgelegt. Ein Zentrum oder Geburtsjahr wird
nicht aus dem Digest abgeleitet.

Die unabhängige Personenquelle enthält 48 synthetische Personen und 46 kodierte
Tupel. Ihre Daten werden aus einem öffentlichen, festen Hilfsquellen-Seed erzeugt,
bevor die privaten Subject-Seeds gezogen werden. Pro Versuch enthält der Generator
18 Personen: drei verschiedene Personen mit identischem kodierten Tupel, zwölf
zufällig ausgewählte weitere Hilfskorpus-Personen, zwei Personen außerhalb des
Korpus und einen verborgenen Zwilling einer abwesenden Hilfskorpus-Person. Eine
der äußeren Personen hat Geburtstag 1889. Je Person entstehen zwei Datensätze
je Zentrum und Generation: 144 Zeilen pro Versuch, zwei Zentren und je eine
aktive/retained Generation. Die Zeilen sind zufällig permutiert, ihre IDs opak.

Die drei Subject-Seeds sind unabhängig; die Überlappung ist absichtlich teilweise
geschlossen. Zwölf Mitglieder werden je Versuch erneut aus demselben Korpus
gezogen; Personen können sich zwischen Versuchen wiederholen. Die sechs festen
Randfälle sind keine zusätzlichen unabhängigen Populationsbeobachtungen. Die
Generatorregeln/Seeds/Ground Truth gehören nicht zum Worker-Eingang. Das hier
nachträglich veröffentlichte Generatorprogramm kann die festen Einschlussstrata
offenlegen und darf nicht als zusätzliche Eingabe dieser ursprünglichen Trials
interpretiert werden.

Der Worker erhält ausschließlich sein typisiertes Bündel und sein festes Programm.
Ein neuer Interpreter, bereinigte Umgebung, geschlossene Zusatzdeskriptoren und
Linux Landlock sperren Dateilesen vor Eingabeverarbeitung. Vier Leseproben wurden
in jedem Worker erfolgreich verweigert. Effektive Grenzen und verbleibende
Netzwerk-/Administratorannahmen stehen in [README.md](README.md). Eine zweite
vollständige Ausführung mit denselben privaten Seeds/Schlüsseln reproduzierte
alle 48 Fallauswertungen, abgesehen von Zeit und Speicher. Ein separater Scorer
prüfte alle 48 archivierten Ergebnisse erneut ohne neue Angriffe.

Die vollständigen **öffentlichen Wissensmanifeste pro Versuch und Fall** liegen
mit allen Baselines und Angreiferausgaben im
[öffentlichen Archiv](evidence/public_bundle.tar.gz). Sie nennen Schlüssel-ID,
UTF-8-Konvention, exponierte Zeilen/Digests, Zentrum/Generation/Schema,
Kandidatenquelle, vorhandene Labels und erlaubte Berechnungen. Exakte Schlüssel
stehen ausschließlich in den eingeschränkten Angreiferbündeln/private-Dateien.
Alle Schlüssel stammen unabhängig von Subject-Seeds aus `secrets.token_hex(32)`;
HMAC verwendet die 64 Zeichen als UTF-8, nicht die 32 hex-dekodierten Bytes.

| Bedingung | Vorher verfügbar | Rolle des Schlüssels |
| --- | --- | --- |
| A | Unbeschriftete Zeilen, bekannte Zentren, generisches Namensmodell und breiter Datumsraum | Berechnet Kandidatendigest; kein Personenregister vorhanden |
| A, Zentrum unbekannt | Gleiche Daten, Zeilenzentrum verborgen; unabhängige Liste a/b/c | Enumeriert Liste; kein Zentrum aus Digest „gelesen“ |
| B | Unbeschriftete Zeilen und unabhängige Namen/Daten/Personenkennungen, keine Mitgliedschaftslabels | Prüft diese zusätzlichen Kandidaten |
| C | Erste vier generische Namenspaare, erklärter Datumsprior 1970–1990 | Getrennte engere Annahme; keine Zielnamen/-jahre nachgetragen |
| D | Sämtliche Identität-Zeilen-Mappings bereits bekannt | Reproduktion erhält null Neuheitskredit |
| Oracle | Gleiche B-Population/Zeilen/Zentren, kein Schlüssel; 32 erlaubte API-Abfragen | Separates Modell, Vergleich mit exakt 32 lokalen Auswertungen |

Die Baseline benutzt direkte Labels und Digestgleichheit zur Weitergabe bekannter
Identität-Token-Zuordnungen. Mehrdeutige Weitergabe bleibt mehrdeutig; direkte
Labels bestimmen die betreffende Zeile. Die kleinen Testfälle prüfen auch eine
**teilweise** beschriftete Quelle mit zusätzlicher unbeschrifteter gleicher Zeile.
A–C enthalten keine versteckten Zeilenlabels oder demographischen Zielmetadaten.
Generationsfingerprints bestätigen gegebenenfalls einen Schlüsselkontext, keine
Patientenidentität; sie werden nicht als Identifikationsgewinn gezählt.

## Gemessene zusätzliche Zuordnungen

Alle folgenden Summen beziehen sich auf drei Versuche. Je Hauptfall sind 432
Zielzeilen exponiert. „Treffer“ bedeutet passende Zeilendigests, nicht eindeutige
Personen. Die Baseline hatte außer D keine richtigen Personen-Zeilen-Mappings.

| Fall | Digest-Treffer | Neue richtige Mappings | Falsche Mappings | Richtige/falsche Mitgliedschaftsbehauptungen |
| --- | ---: | ---: | ---: | ---: |
| A, bekannte Zentren | 84 | 0 | 0 | nicht anwendbar: kein Personenregister |
| A, Zentrumsliste a/b/c | 68 | 0 | 0 | nicht anwendbar |
| B, gemeinsamer Schlüssel | 192 | 144 | 12 | 36 / 3 |
| B, nur unabhängiger Schlüssel von a exponiert | 96 | 72 | 6 | 36 / 3 |
| B, retained Schlüssel exponiert | 192 | 144 | 12 | 36 / 3 |
| B, falscher Schlüssel | 0 | 0 | 0 | 0 / 0 |
| B, gemeinsame Architektur, 32 Auswertungen | 92 | 44 | 12 | 11 / 3 |
| B, zentrales Oracle, 32 Abfragen | 92 | 44 | 12 | 11 / 3 |
| C, enger unabhängiger Prior | 8 | 0 | 0 | nicht anwendbar |
| D, vollständig beschriftet | 192 | **0** | 0 | **0 neue** / 0 |
| Examination, unabhängige Zeitliste | 64 | 46 | 4 | 29 / 3 |
| Examination, zusätzlich Kandidat-Patientenfingerprints | 64 | 46 | 4 | 29 / 3 |
| Examiner, separate synthetische Personalliste | 192 | 0 | 0 | alle 54 Kandidaten-Versuchspaare mehrdeutig |

D hatte bereits **432 korrekte Mappings und 54 bekannte Mitgliedschaften**.
Eine erneute Berechnung verändert diesen Wissensstand nicht. Die B-Mitgliedschaften
werden konservativ als *bedingte* Personenbehauptungen behandelt: Eindeutigkeit
innerhalb der Hilfsquelle beweist keine Eindeutigkeit in der Welt. Der versteckte
Zwilling erzeugt deshalb eine tatsächliche Fehlattribution. Drei bekannte
gleichnamige/gleichdatierte Personen bleiben je Versuch mehrdeutig.

Bei B/shared stehen 36 richtige Mitgliedschaftsbehauptungen 54 Zielpersonen-
Versuchspaaren gegenüber, davon 45 im Hilfskorpus vertreten. Von 144 Hilfskandidaten-
Versuchspaaren sind 36 als enthalten behauptet, drei fälschlich enthalten, neun
mehrdeutig und 96 unbekannt. **Es gibt keine Abwesenheitsbehauptung**, also keine
als Erfolg zu interpretierenden echten Negativen. Die Null bei falschen Negativen
bedeutet ebenfalls nicht, dass alle Mitglieder erkannt wurden.

A findet insgesamt 15 kodierte Tupel-Versuchspaare; mit unbekanntem Zentrum sind
es elf, unter C zwei. Das ist eine relevante Attributoffenlegung, jedoch keine
durch den Scorer nachträglich erzeugte Personenbehauptung. Originale Randzeichen
oder Unicode-Normalisierung lassen sich daraus nicht eindeutig rekonstruieren.

## Abdeckung und Arbeitsgrenzen

| Fall | Alle Zeilen | Im kompatiblen Kandidatenraum | Davon tatsächlich erreicht | Außerhalb des Kandidatenraums | Falscher/nicht verfügbarer Kontext |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 432 | 192 | 84 | 24 | 216 |
| A, Zentrum unbekannt | 432 | 192 | 68 | 24 | 216 |
| B/shared | 432 | 192 | 192 | 24 | 216 |
| B/independent | 432 | 96 | 96 | 12 | 324 |
| C | 432 | 8 | 8 | 208 | 216 |
| Examination | 432 | 64 | 64 | 152 | 216 |

Für A bleiben 108 Zeilen im vollständigen Kandidatenraum jenseits des erreichten
Präfixes, bei unbekanntem Zentrum 124. Alle erreichten, kompatiblen und repräsentierten
Ziele passten; `reached_no_match` war null. Nicht exponierte andere Zentrumsschlüssel
und retained Generationen sind keine erschöpften gültigen Suchen. Der eigene
retained-Schlüssel-Fall identifiziert gerade diese historische Generation und
nicht automatisch die aktive. Der Legacy-Fall markiert 432 Zeilen ausdrücklich als
`unsupported_scheme`, ohne einen erfundenen historischen Algorithmus zu testen.

Der separate Einzelzielversuch betrachtet drei zufällige einzelne Zeilen, davon
eine durch den aktiven Schlüssel/Korpus erreichbar und zwei historisch. Er gewinnt
eine Zuordnung bei insgesamt 276 Kandidat-Kontext-Auswertungen. Der Populationsscan
benötigt dieselben 276 Auswertungen für 432 Zeilen: Kandidaten werden einmal je
Kontext berechnet und im ganzen Zielset nachgeschlagen. Kosten dürfen nicht mit der
Zielzahl multipliziert werden. Schlüssel-ohne-Dump: null Vergleiche und null Gewinne.

Hardware: Intel Core i5-12450H, zwölf logische CPUs; Python 3.12.14,
Linux 6.12.112 x86_64. Ein Worker rechnet seriell. Fremdlast wurde nicht kontrolliert.

| Suche | Auswertungen, drei Versuche | Suchzeit gesamt | HMAC-Primitive |
| --- | ---: | ---: | ---: |
| A | 750.000 | 3,703 s | 750.000 |
| A, Zentrum unbekannt | 750.000 | 3,559 s | 750.000 |
| C | 184.080 | 0,949 s | 184.080 |
| B/shared | 276 | 0,00233 s | 276 |
| Examination | 1.104 | 0,00737 s | 1.380 |
| Examination mit zusätzlichen Patientenfingerprints | 1.104 | 0,00573 s | 1.104 |

A erreichte pro Versuch 250.000 Auswertungen in 1,188–1,279 Sekunden, entsprechend
etwa 195.500–210.500 Auswertungen/s. Das ist die gemessene Offline-Schleife,
einschließlich Kodierung/Lookup, nicht Produktionshelper-Durchsatz. Kurze B-Laufzeiten
sind besonders rauschanfällig. Höchster beobachteter Prozess-RSS: 71.152 KiB,
einschließlich Interpreter/Prozessstart. Es wurden keine Laufzeitextrapolationen
zu klinischen Populationen und keine Messungen einer Kompromittierungswahrscheinlichkeit
vorgenommen. Grenzen und Stop-Gründe stehen unverändert in jeder Ergebnisdatei.

## Weitere Hashfunktionen und Konformität

Die Differentialprüfung verwendet den unveränderten Produktionshelper für alle
Zielgenerierungen. Geprüft wurden JSON/UTF-8, Strip-Regeln, Unicodeformen,
Anführungszeichen/Backslash, DOB als `datetime` ohne Zeitzonenverschiebung,
Examinationsdatum als Datum und Datum/Zeit mit Offset/Mikrosekunden, `None` und
andere Laufzeitwerte, Hex-/Base64-aussehende Schlüssel als UTF-8 sowie Zentren und
enrollte historische Testgenerationen. Ungültige aktive Schlüssel werden abgewiesen.
Die absichtlich schwache historische Testgeneration bleibt ein reiner
Kompatibilitätskontrollfall, kein Experiment zur Gewinnung eines starken Schlüssels.

`SensitiveMeta.examination_date` ist ein `DateField`
(`endoreg_db/models/metadata/sensitive_meta.py:75`). Der Aufrufer
`calculate_examination_hash` lehnt ein fehlendes Datum ab und reicht den Wert weiter
(`sensitive_meta_logic.py:330`); der Helper selbst unterstützt zusätzliche
Laufzeitrepräsentationen. `secret_rotation/identity.py:160` verlangt ebenfalls ein
vorhandenes ursprüngliches Datum. Die synthetischen Examination-Ziele umfassen
Datum, einen nicht geratenen anderen Tag und Offset/Mikrosekunden. Der unabhängige
Vier-Werte-Kalender enthält nur einen davon genau. Zeitpräzision wird nicht still
auf Mitternacht reduziert. Bereits exponierte Kandidat-Patientenfingerprints sind
ein **separater zusätzlicher Wissenseingang**, auch für Nichtmitglieder; ihr Cache
spart innere HMACs, liefert aber nicht von selbst Zielmitgliedschaft.

Examiner-Hashes haben kein Geburtsdatum. Die separate Personalliste enthält
absichtlich mehrere Personen pro Namenspaar; alle erkannten Namensgruppen bleiben
personenbezogen mehrdeutig. Diese Ergebnisse sind keine Patientenidentifikation.

Der exakte Tupelkontrollfall benötigt eine Auswertung. Der alte Kontrollfall
`Synthetic Person`, vorgegebenes Jahr 1980, durchsucht genau 366 Tage. Beide haben
ein zuvor geliefertes Identität-Zeilen-Mapping und gewinnen **null** neue Mappings.
Das Jahr wurde dort nicht aus einem Digest ermittelt.

Die unverschlüsselten Byte-/Datei-Kontrollen erkennen zwei von drei unabhängig
gewählten synthetischen UTF-8-Artefakten, einschließlich des abschließenden Newline.
Ohne Personenzuordnung entsteht keine Personenbehauptung. Eine separat gelieferte
Datei-Person-Zuordnung erlaubt die zugehörige Zuordnung bereits der Baseline.
Ergebnisse mit/ohne HMAC-Schlüssel sind identisch: **null Schlüsselbeitrag**.
Digestgleichheit rekonstruiert keine Bytes.

`get_bytes_hash` übernimmt Bytes unverändert. Reale Aufrufer haben unterschiedliche
Domänen, etwa UTF-8 aus `fhir_r4.py:71`, verkürzte Digests in
`sap_ish_clinical.py:109` und serialisierte Daten in `medical_ledger.py:479`.
Die Artefaktkontrolle behauptet nicht, alle diese Domänen zu repräsentieren.
`stable_file_identity` arbeitet nativ: reguläre Datei, kein Symlink, stabile
Vorher-/Nachher-Metadaten; bei erkanntem Verschlüsselungsheader wird authentifiziert
entschlüsselt und Klartext gehasht. Daher ist nicht jeder lokale Pfad gleich einem
rohen Ciphertext-SHA-256. Gemessen wurde hier der lokale synthetische Klartextpfad;
gespeicherte verschlüsselte Dateien wurden nicht als klinische Fixtures geöffnet.

## Verifikation, Grenzen und Interpretation

16 fokussierte Tests bestanden, darunter echte Dateizugriffssperren, Baseline-
Propagation, Ambiguität, falsche Zwilling-Zuordnung, Null-Neuheit, Budgets,
Schema-/Kontextfehler, Oracle-Parität und Start ohne reale Settings/Keyring-Datei.
Repository-Pyright: null Fehler, 24 bestehende Warnungen; fokussierter Check:
null Fehler/Warnungen. Die Strukturprüfung bestand für 66 Manifeste. Details und
Replay-/Publikationsprüfung: [verification.json](evidence/verification.json).

Die eingefügten TODO-Kommentare in `run.py` benennen die fehlende separate
`hashs.py`-Datei, die fehlende empirische Kandidatenverteilung und die hier nicht
gemessene gespeicherte/verschlüsselte Datei-Integration. Sie verweisen auf konkrete
Quelldateien/-stellen; dies ist keine Freigabe klinischer Reidentifikationsraten.
Produktions-Import-/Streaming-Verhalten wurde nicht verändert; eine vollständige
klinische Import- oder Deployment-Suite ist kein Ergebnis dieses isolierten Laufs.

Das vorhandene Lean-Ergebnis (`patient_matching_lean/PatientMatching/Privacy.lean`)
betrifft den explizit offengelegten exakten Matching-Ausgang. Es liefert weder eine
numerische Erfolgsquote dieser Angriffe noch einen Beweis, dass Verschlüsselung
generell unsicher sein müsse. Der Oracle-Vergleich misst ausschließlich die
Offenlegung unter 32 ausdrücklich erlaubten Anfragen. Die reale zentrale
Identitätsschlüsselbereitstellung bleibt unverändert deaktiviert.

**Antwort auf die Leitfrage:** Mit diesem Schlüssel, diesem unbeschrifteten Dump,
diesem zusätzlichen Korpus und diesem Budget werden die oben gezählten neuen
Zuordnungen und bedingten Mitgliedschaftsbehauptungen möglich. Fehlende Korpuseinträge,
unexponierte andere Schlüssel/Generationen und nicht getestete Zeitrepräsentationen
bleiben unerkannt. Schon gelieferte Mappings zählen nicht erneut. Ohne zusätzliche
Personenquelle sind gefundene kodierte Attribute ein eigenes Ergebnis, keine vom
Scorer erfundene eindeutige Personenidentifikation.
