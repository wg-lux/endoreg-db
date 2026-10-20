# Empfindlichkeit der Dateihashprüfung gegenüber Pixeländerungen

## Gegenstand und Einordnung

Dieses Material beschreibt einen deterministischen technischen Validierungsversuch der
SHA-256-Dateihashprüfung in Endoreg-db. Untersucht wird, ob kontrollierte Änderungen an
synthetischen PNG-Bildern und deren Metadaten die Gleichheit der Dateihashes verändern.
Es handelt sich weder um eine klinische Studie noch um eine Schätzung menschlicher
Bildähnlichkeit. Die kleinste untersuchte Pixeländerung beträgt eine Intensitätsstufe
in einem Farbkanal eines Pixels. Ein universeller Ähnlichkeitsschwellenwert wird nicht
geschätzt. Die Scope- und Verifikationsevidenz wird ausschließlich unter
`image_content_hash_pixel_experiment` in `feature-tracking/CodeQuality.yml` geführt.

## Versuchsprotokoll

Zwei RGB-Ausgangsbilder mit Seitenlängen 8 und 16 Pixeln werden aus der Bytefolge
`bytes(index % 251 for index in range(side * side * 3))` erzeugt. Die Kanäle sind
8-Bit-Ganzzahlen. Ein separater Metadatenversuch verwendet ein drittes synthetisches
8 × 8-Bild aus `bytes(range(192))`. Es werden keine Patientendaten verwendet.

Für jede Seitenlänge werden Rot, Grün und Blau in getrennten Reihen verändert. In
Generation g wird im g-ten Pixel in zeilenweiser Reihenfolge genau der untersuchte
Kanal um eins erhöht; vorherige Änderungen bleiben erhalten. Alle Ausgangswerte
liegen höchstens bei 250, sodass kein Überlauf entsteht. Jede Reihe enthält N = side²
Vorwärtsgenerationen. Die sechs Reihen ergeben 3 × (64 + 256) = 960 Zustände.

Pillow kodiert jeden Zustand mit `Image.save(format="PNG", pnginfo=None)` und den
Defaults der aufgezeichneten Pillow-Version. Eine Dekodierung prüft anschließend die
exakte Übereinstimmung mit den vorgesehenen Pixelbytes. Die Datei wird über den
atomaren Repository-Wrapper geschrieben. `get_file_hash` berechnet ihren Hash mit
dem tatsächlich installierten nativen Backend. Pro Reihe wird außerdem eine
byteidentische Kopie unter anderem Dateinamen geprüft.

Die Änderungen werden anschließend in umgekehrter Reihenfolge zurückgenommen.
Jeder Rücknahmeschritt muss den Hash des entsprechenden früheren Zustands reproduzieren.
Der letzte Zustand muss zusätzlich dieselben Datei-Bytes wie das Original besitzen.
Beim Metadatenkontrollpaar wird ausschließlich ein PNG-Textfeld mit dem Schlüssel
`experiment` und dem Wert `identical pixels, different metadata` ergänzt.

Der Exporter ersetzt während des Testlaufs ausschließlich den lokalen Verweis auf
`get_file_hash` im Testmodul durch einen beobachtenden Wrapper. Dieser ruft den echten
Helper auf und gibt dessen unverändertes Ergebnis zurück. Die verwendeten Dateien
werden unmittelbar archiviert und erneut mit demselben Helper geprüft. Der Exporter
ist damit keine unabhängige kryptografische Referenzimplementierung. Er ergänzt die
bestehenden Assertions um ein überprüfbares Messarchiv.

## Endpunkte und Auswertung

Primärer Endpunkt ist die exakte Gleichheit des hexadezimalen Dateihashes mit dem
Ausgangshash derselben Reihe. Zusätzlich prüft der Test, dass innerhalb jeder
Vorwärtsreihe kein früherer Hash erneut auftritt. Vergleiche über unterschiedliche
Reihen hinweg sind nicht Teil dieses Endpunkts.

Der Export erfasst deskriptiv die Hamming-Distanz der 256 Digest-Bits zum Ausgangshash.
Sie wird als Anzahl der Einsen im bitweisen exklusiven Oder beider Hashwerte berechnet.
Sie ist kein Maß der visuellen Bildähnlichkeit. Es wird kein Schwellenwert an diese
Distanz angepasst und kein Test auf eine theoretische Verteilung durchgeführt.

Die mittlere quadratische Kanalabweichung wird über alle 3 × N Kanalwerte berechnet:

`MSE = sum((original_channel - current_channel)²) / (3 × N)`.

Für g kumulative Änderungen um eine Stufe gilt MSE = g/(3N). Der Anteil veränderter
Pixel ist g/N. Beim ersten Schritt sind dies 1,5625 % für 8 × 8 und 0,390625 % für
16 × 16. Die Kanaländerung relativ zur gesamten 8-Bit-Skala beträgt 1/255.

Die 960 Zustände sind voneinander abhängige Beobachtungen aus zwei Ausgangsbildern.
Die 960 Rücknahmeschritte reproduzieren bereits besuchte Zustände. Weder diese Schritte
noch wiederholte Testausführungen zählen als zusätzliche unabhängige Bildstichproben.
Es werden daher keine populationsbezogenen Konfidenzintervalle, p-Werte oder
allgemeinen Sensitivitäts- und Spezifitätsschätzungen angegeben.

## Ergebnistabellen und Bildlegenden

Die konkreten Messwerte befinden sich in `measurements.csv`, die sieben
Versuchsreihen in `summary.csv`. Der vollständige Export enthält 1.934 Beobachtungen:
7 Ausgangszustände, 6 Kopiekontrollen, 960 Vorwärtsgenerationen, 960 Rücknahmeschritte
und 1 Metadatenänderung. Gleichartige Dateien werden pro Beobachtung separat abgelegt;
die Dateizahl ist keine Zahl unabhängiger oder einzigartiger Bilder.

**Abbildung 1, `example_images`.** Ausgangsbild und erste Änderung im roten Kanal für
beide Seitenlängen. Die Vergrößerung verwendet den nächsten Nachbarn und keine
Glättung. Rechts kennzeichnet eine binäre Maske den veränderten Pixel in Weiß;
diese Maske zeigt den Ort, nicht die tatsächliche Intensität der Änderung.

**Abbildung 2, `hash_distance`.** Hamming-Distanz zum Ausgangshash über der Anzahl
kumulativ geänderter Pixel, getrennt nach Bildgröße und Farbkanal. Punkte sind
abhängige Zustände einer deterministischen Reihe, keine unabhängigen Wiederholungen.

**Abbildung 3, `controls`.** Anteil gleicher Ausgangshashes bei byteidentischen
Kopien, kumulativen Pixeländerungen und einer Metadatenänderung. Die unterschiedlichen
Nenner werden direkt angegeben; die Darstellung ist rein deskriptiv und enthält
keine inferenzstatistischen Fehlerbalken. Die Metadatenkontrolle hat null geänderte Pixel.

Alle Abbildungen werden als PDF und SVG für Vektorausgabe sowie als PNG mit 300 dpi
bereitgestellt. Die originalen kleinen Versuchs-PNGs liegen unvergrößert unter `images/`.

## Datenwörterbuch

| Spalte | Bedeutung |
| --- | --- |
| series | Pytest-Fallname mit Kanal und Seitenlänge beziehungsweise Metadatenkontrolle |
| step | Nullbasierte Reihenfolge der tatsächlich beobachteten Hashaufrufe innerhalb der Reihe |
| phase | baseline, copy, forward, reverse oder metadata |
| side | Bildbreite und Bildhöhe in Pixeln |
| changed_pixels | Pixel mit mindestens einem abweichenden Kanal gegenüber dem Ausgangsbild |
| changed_channels | Anzahl abweichender Kanalwerte gegenüber dem Ausgangsbild |
| changed_pixels_previous | Abweichende Pixel gegenüber der vorherigen Beobachtung; Ausgangszustand 0 |
| max_channel_delta | Maximale absolute Kanalabweichung gegenüber dem Ausgangsbild, Intensitätsstufen |
| mse | Mittlere quadratische Kanalabweichung in quadrierten Intensitätsstufen |
| hash_distance_bits | Hamming-Distanz zum Ausgangshash, 0 bis 256 Bit |
| matches_original | Exakte Hashgleichheit mit dem Ausgangszustand derselben Reihe |
| sha256 | Vollständiger vom produktiven Helper gelieferter Dateihash |
| bytes | Länge der PNG-Datei in Bytes |
| image | Relativer Pfad zur tatsächlich gehashten und archivierten Datei |

`step` ist nicht mit der kumulativen Pixelzahl gleichzusetzen: Schritt 1 ist in den
Pixelreihen die unveränderte Kopie, Schritt 2 die erste Pixeländerung.

## Interpretation und Grenzen

Die kleinste untersuchte und unterschiedene Pixeländerung ist eine Intensitätsstufe
in einem Kanal eines Pixels. Nur der erste Vorwärtsschritt jeder Reihe unterscheidet
sich vom Original an genau einem Pixel. Spätere Positionen wurden nicht einzeln gegen
das unveränderte Original geprüft. Ein Schwellenwert für beliebige Bilder, Positionen,
Formate, Kodierer oder Skalierungen lässt sich daraus nicht ableiten.

Eine Metadatenänderung kann den Dateihash verändern, obwohl alle Pixel gleich bleiben.
Deshalb beschreibt kein ausschließlich pixelbasierter Schwellenwert die Dateihashgleichheit
vollständig. SHA-256 dient der Integritätsprüfung einer Nachricht beziehungsweise Datei [1].
PNG erlaubt neben verlustfrei kodierten Pixelwerten zusätzliche Textinformationen [2].
Die Pillow-Schnittstelle unterstützt solche zusätzlichen Informationen über `pnginfo` [3].

Das Experiment bestätigt erwartetes Implementierungsverhalten und erhebt keinen Anspruch
auf eine neue kryptografische Eigenschaft. Es untersucht keine perzeptuellen Hashverfahren,
JPEG-Rekodierung, klinischen Bilder oder menschliche Wahrnehmung. Keine beobachtete
Kollision ist kein Beweis allgemeiner Kollisionsfreiheit. Die Testlaufzeit ist kein
Hashleistungsbenchmark, da Kodierung, Assertions und Archivierung enthalten sind.

## Reproduktion und Archivstruktur

Aus dem Repository-Root im eingerichteten Projektumfeld:

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/python -m scripts.collect_image_hash_publication
```

Der Exporter verwendet den kanonischen `get_runtime_paths().export_dir` und erzeugt
einen neuen Ordner mit Zeitstempel und eindeutiger Kennung. Pytest verwendet weiterhin
seine eigenen isolierten Laufzeitpfade. Bereits vorhandene Exporte werden nicht überschrieben.
Die benötigten Nix-Bibliotheken und Binutils müssen im Shell-Suchpfad liegen; der
Testfall benötigt dank `no_db` keine Datenbank. Der Lauf muss mit sieben bestandenen
Tests enden. Ein fehlgeschlagener Testlauf beendet den Export mit einem Fehler.

| Datei oder Ordner | Inhalt |
| --- | --- |
| PROTOKOLL.md | Dieses Protokoll einschließlich Datenwörterbuch und Literatur |
| images/ | Alle 1.934 tatsächlich gehashten PNG-Dateien |
| measurements.csv | Eine Zeile pro Hashbeobachtung |
| summary.csv | Zusammenfassung der sieben Reihen |
| figures/ | Drei Abbildungen, jeweils PDF, SVG und PNG |
| code/ | Ausgewählte exakte Quellcode-Snapshots und Repository-Lizenz |
| environment.yml | Versionen, Commit, Hinweis auf uncommittete Änderungen und nativer Backend-Hash |
| pytest.log | Ausgabe der experimentellen Testausführung |
| checksums.yml | SHA-256-Prüfsummen der Archivdateien außer der Prüfsummenliste selbst |

Der Ordner wird zusätzlich als ZIP mit separater SHA-256-Prüfsumme ausgegeben. Die
Prüfsummen unterstützen die Integritätskontrolle; sie sind keine digitale Signatur.
Der erfasste Commit allein beschreibt bei `dirty_worktree: true` nicht die gesamten
ausgeführten Quellen. Die beigefügten Snapshots dokumentieren die ausgewählten relevanten
Dateien; das vollständige Repository, alle transitive Abhängigkeiten und die native
Binärdatei sind nicht Teil des Pakets. Ein identischer Lauf in einer unabhängig neu
aufgesetzten Umgebung wird dadurch nicht behauptet.

## Literatur

1. National Institute of Standards and Technology. *Secure Hash Standard (SHS)*.
   FIPS PUB 180-4, August 2015. DOI: [10.6028/NIST.FIPS.180-4](https://doi.org/10.6028/NIST.FIPS.180-4).
   [Offizielle Publikationsseite](https://csrc.nist.gov/pubs/fips/180-4/upd1/final).
2. World Wide Web Consortium. *Portable Network Graphics (PNG) Specification, Third Edition*.
   W3C Recommendation, 24. Juni 2025.
   [Versionierte Spezifikation](https://www.w3.org/TR/2025/REC-png-3-20250624/).
3. Pillow contributors. *Image file formats: PNG*. Online-Dokumentation,
   [PNG-Speicheroptionen](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#png).
   Die tatsächlich eingesetzte Paketversion steht in `environment.yml`; die Online-Dokumentation
   kann eine neuere Version beschreiben.

Quellenzugriff: 16. Oktober 2026. Code und Protokoll wurden mit KI-Unterstützung erstellt;
wissenschaftliche Interpretation und endgültige Publikationsfreigabe liegen bei den Autoren.
