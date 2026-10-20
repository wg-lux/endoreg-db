# Reversibilität und Kollisionen des Sensitive Meta Hash

## Aktuelle Einordnung und Policy vom 19.10.2026

Patienten- und Untersuchungshashes sind **lokale Pseudonyme**, keine anonymen
Exportkennungen. Sie bleiben einschließlich Identitätsfingerprints und historischer
Hash-Aliase am Quellknoten. Auch ein verschlüsseltes Transferpaket, ein gekürzter
Hash oder ein umbenanntes Pseudonym erlaubt keine Weitergabe an Hub, Browser oder
Forschungsexport. Datei-Inhaltsprüfsummen erfüllen einen anderen Integritätszweck;
ihre Zulässigkeit beweist jedoch keine Anonymität der exportierten Daten.

Die verbindlichen Verträge sind
[lokale Patientenidentität](../../docs/source_patient_matching.md) und
[lokale Identitäts-Keyrings](../../docs/identity_ring_enrollment.md). Umfang und
Abnahme bleiben ausschließlich in [Anonymization.yml](../Anonymization.yml),
insbesondere `identity_hash_response_boundary`, `central_identity_salts_disabled`,
`strict_legacy_hmac_inventory` und `compromised_identity_salt_migration`, sowie
[HubTransfer.yml](../HubTransfer.yml), `local_identity_hash_boundary`.

Die frühere Planung zentral verteilter Zentrumssalts ist damit abgelöst.
Vault-/SecretSpec-Verteilung von Identitätssalts darf nicht wieder aktiviert
werden. Etablierte lokale Schlüssel bleiben erhalten; fehlendes Material wird
weder automatisch neu erzeugt noch durch zentral bezogene Werte ersetzt.
Historische Salts sind am Quellzentrum weiterhin für die Wiedererkennung nötig,
auch wenn direkte Identifikatoren gelöscht wurden. Ihre Unterstützung gestattet
weder alte Authentifizierungsschlüssel noch neue Identitäten mit kompromittierten
Salts. Historische Medienentschlüsselungsschlüssel haben eine getrennte Aufgabe.

### Aktuelle Berechnung und Aussagegrenzen

Der aktuelle demografische Schreibpfad verwendet unmittelbar:

```text
K = UTF-8 des validierten lokalen Identitätsgeheimnisses
M = UTF-8(JSON(["patient-identity-v1", trim(Vorname), trim(Nachname),
               ISO(Geburtsdatum), trim(center_key)],
              ensure_ascii=False, separators=(",", ":")))
patient_hash = hex(HMAC-SHA-256(K, M))
```

Belege: `endoreg_db/utils/hashs.py`, Funktionen
`get_patient_identity_fingerprint` und `calculate_patient_hash`. Das Geburtsdatum
steht einmal in der Patientennachricht. JSON-Feldgrenzen beseitigen die unten
beschriebene Legacy-Mehrdeutigkeit; das frühere Doppelhashing ist kein aktueller
Patientenvertrag. Der Untersuchungshash verwendet eine gesondert markierte
HMAC-Nachricht mit Patienten-HMAC, Untersuchungsdatum und Zentrum.

`endoreg_db/utils/verify_salt.py::validate_identity_salt` prüft aktive Schlüssel
auf Mindestlänge, gesperrte Muster und Bytevielfalt. Diese Prüfungen beweisen
keine zufällige Erzeugung. Explizit registrierte historische Lesegenerationen
bleiben in `hashs.py::_get_active_salt_bytes` unterstützt. Der Loader
`config/secret_keyring.py::configured_identity_keyring` weist zentrale Bundles
und den Modus `secretspec` zurück. Die Antwort-/Transfergrenze liegt in
`services/privacy/response_boundary.py`; die vollständigen Vertragsdetails und
Ausnahmen für Inhaltsprüfsummen gehören in die oben verlinkten Quellen.

Bei bekanntem Geheimnis bleibt eine begrenzte Kandidatensuche auch gegen HMAC
möglich. Homonyme bleiben fachlich mehrdeutig, Schreibvarianten können Identitäten
aufspalten. Für den aktuellen einzelnen 256-Bit-HMAC gilt die unten erläuterte
idealisierte Geburtstagsabschätzung; die Doppelhash-Obergrenze betrifft nur den
historischen Algorithmus. Weder diese Modellrechnung noch die Übertragungssperre
beweist die Anonymität vollständiger Exporte.

Migration und lokale Wiedererkennung müssen historische Generationen und
zentrumsgebundene Aliase erhalten, Konflikte ablehnen und klinische Beziehungen
bewahren. Hub-Reidentifikation anhand übertragener Hashes ist kein zulässiges
Migrationsziel mehr. Ein anderer Transfervertrag benötigt eine separate fachliche
und technische Definition. Rotation schützt bereits entwendete Hash-/Schlüsselkopien
nicht rückwirkend; eine Codeänderung löscht keine bereits verteilten Geheimnisse
oder Hub-Daten und belegt keinen produktiven Migrationsstand.

### Reproduzierbarkeit dieses Abgleichs

Am 19.10.2026 wurde ausschließlich die Dokumentation mit dem lokalen Arbeitsbaum
abgeglichen. Der bestehende Test
`tests/utils/test_sensitive_meta_hash_security.py` prüft inzwischen ausdrücklich
Legacy-Kodierungskollisionen **und deren Trennung im aktuellen HMAC**, Wiederholung,
Salt-Wechsel, 366 Geburtsdaten sowie einen registrierten schwachen historischen
Salt. Ausführung: `devenv shell -- .devenv/state/venv/bin/pytest
tests/utils/test_sensitive_meta_hash_security.py -q --tb=short --show-capture=no
--disable-warnings`: **11 bestanden, 1 Warnung**, 0,26 Sekunden; kein Benchmark
und kein Nachweis für Flottenzustand, Restore oder vollständige Exportabsicherung.

Die [Implementierungsbewertung vom 19.10.2026](../../docs/endoreg-db-threat-model.md)
enthält ergänzende empfängerbezogene Risiken. Ihre Aussagen zu einzelnen
Exportfeldern sind ein Arbeitsbaum-Snapshot und müssen gegen die neuere lokale
Identitätspolicy geprüft werden; sie erlauben keine verbotene Hashausgabe.

## Historische Untersuchung vom 16.10.2026

**Die folgenden Formeln, Zeilenverweise und Befunde beschreiben ausschließlich
den damaligen Stand.** Insbesondere kurze aktive Salts, Doppelhashing und die
Empfehlung eines künftigen HMAC-Wechsels sind keine Beschreibung des aktuellen
Schreibpfads. Die historischen Versuche bleiben als Begründung der Migration
erhalten; aktuelle Bereitschaft und offene Abnahmen stehen nur im Feature-YAML.

Der demografische Patientenhash ist ein deterministisches Pseudonym. Er bietet
keinen allgemeinen Nachweis der Anonymität: Mit bekanntem Salt können mögliche
Identitäten nachgerechnet werden. Die größte konkrete Kollisionsquelle ist die
mehrdeutige Verkettung der Eingabefelder; zufällige kryptografische Kollisionen
sind unter dem Idealmodell dagegen extrem unwahrscheinlich. Der bestehende
HMAC-Fingerprint schützt den regulären Speicherpfad vor unterschiedlichen
Feldaufteilungen, kann aber zwei reale Menschen mit identischen Identitätsdaten
nicht unterscheiden.

Untersuchungsdatum: 16.10.2026. Basis: Arbeitsbaum auf Commit
`08c1ac0da110343dfc6d0314b24998a1937d9aeb`, einschließlich bereits vorhandener
uncommitteter Änderungen. Methodische Vorlage:
`codex://threads/01a143b2-26ac-7ef2-a191-a0a43760aeda`.
Anforderungen und Freigabestatus bleiben ausschließlich in
[Anonymization.yml](../Anonymization.yml), Kriterium
`sensitive_metadata_resolution`. Dies ist eine technische Untersuchung, keine
Produktionsfreigabe oder rechtliche Bewertung.

## Tatsächliche Berechnung

Mit `H = SHA-256`, `hex` als Hexadezimaldarstellung, `||` als Verkettung,
Vorname `V`, Nachname `N`, ISO-Geburtsdatum `D`, Zentrumsname `C` und Salt `S`
berechnet der demografische Pfad:

```text
M = UTF-8(V || N || D || C || D || S)
calculate_patient_hash = hex(H(M))
SensitiveMeta.patient_hash = hex(H(UTF-8(hex(H(M)))))
```

Das Geburtsdatum kommt zweimal vor. Diese Wiederholung liefert keine zusätzliche
Entropie. Der zweite SHA-256-Aufruf verarbeitet die 64 ASCII-Hexzeichen des ersten
Digests, nicht dessen 32 Rohbytes. Die Ausgabe bleibt 256 Bit, nicht 512 Bit.
Belege: `endoreg_db/utils/hashs.py:118–159` und
`endoreg_db/models/metadata/sensitive_meta_logic.py:295–318`.

Der Untersuchungshash fügt das Untersuchungsdatum vor dem Salt ein und wird
ebenfalls doppelt gehasht (`sensitive_meta_logic.py:321–356`). Ein Patientenhash
identifiziert somit weder einen Bericht noch zwingend eine einzelne Untersuchung.
Externe, quellenspezifische Patientenkennungen besitzen einen eigenen Pfad und
fallen nicht unter die obige demografische Formel.

Der zusätzliche `identity_fingerprint` ist dagegen ein HMAC-SHA-256 über eine
JSON-Liste mit Versionskennung, getrennten Namen, ISO-Geburtsdatum und numerischer
Zentrums-ID (`hashs.py:33–52`). Der `identity_salt_fingerprint` ist ein HMAC desselben
Schlüsselmaterials über die konstante Zeichenfolge `endoreg-identity-salt-v1`
(`hashs.py:25–30`). Die vier Hash-/Fingerprintfelder speichern jeweils bis zu
64 Zeichen (`models/metadata/sensitive_meta.py:145–157`). HMAC ist eine standardisierte
Konstruktion mit geheimem Schlüssel; sie ist nicht mit bloßem Anhängen eines Salt
gleichzusetzen. [RFC 2104](https://www.rfc-editor.org/rfc/rfc2104.txt)

## Befunde nach Sicherheitsrelevanz

### 1 Hohe Relevanz bei bekanntem oder schwachem Salt

Ein Hash ist keine Verschlüsselung mit Rückwärtsfunktion. Reidentifikation kann
dennoch durch einen Vorwärtsvergleich erfolgen: Kandidaten bilden, mit demselben
Salt berechnen, mit dem Ziel vergleichen. Sind Name und Zentrum bekannt, besteht
die Suche möglicherweise nur noch aus Geburtsdaten. Der Test verwendet einen
bekannten Namen, ein Zentrum und die 366 Tage des Jahres 1980. Das Ziel ist
synthetisch und ausdrücklich Teil der Kandidatenmenge. Ein Treffer identifiziert
ein kompatibles Eingabetupel, nicht zwingend eine eindeutige reale Person.

Der als Salt bezeichnete Wert wirkt hier als gemeinsames Geheimnis. Bei starkem,
unbekanntem und geschütztem Schlüssel ist der einfache Offline-Abgleich ohne
Schlüssel nicht möglich; ein erreichbarer Berechnungsdienst könnte jedoch als
Vergleichsorakel dienen. Ein solcher Dienst wurde hier nicht nachgewiesen.
Bei offengelegtem Salt schützt auch der HMAC-Fingerprint nicht vor Kandidatenprüfung.
Zweimal schnelles SHA-256 ersetzt keine speicherintensive Schlüsselableitung.

`config/secret_keyring.py:60–84` lehnt leere, mehrzeilige und bestimmte Standardwerte
ab, erzwingt aber weder Mindestlänge noch zufällige Erzeugung. Ein einzelnes Zeichen
wird akzeptiert. Wer den gespeicherten Salt-Fingerprint kennt, kann schwache
Salt-Kandidaten anhand der öffentlichen konstanten Nachricht prüfen, ohne einen
Patientennamen zu kennen. Das ist kein HMAC-Bruch, sondern eine Folge erratbarer
Schlüssel. Die tatsächliche Qualität produktiver Schlüssel wurde nicht geprüft.
Private Schlüsseldateien und explizite Fehler bei ungültiger Konfiguration sind
bereits vorhanden (`config/identity_hashing.py:30–70`).

### 2 Hohe Integritätsrelevanz durch mehrdeutige Eingabekodierung

`AB` plus `C` und `A` plus `BC` ergeben bei sonst identischen Feldern dieselbe
Bytefolge vor dem Hashen. Für dieses Paar ist Hashgleichheit garantiert. Es handelt
sich um eine Kodierungskollision, nicht um zwei verschiedene SHA-256-Nachrichten
mit demselben Digest. Jede Aufteilung von `ABCDEFGH` in zwei nichtleere Namen
ergibt denselben Legacy-Hash: sieben Feldaufteilungen und 21 gleiche Paare.

Die reguläre `SensitiveMeta.save()`-Logik berechnet den getrennt kodierten
Fingerprint und prüft vorhandene Zeilen mit gleichem Patientenhash. Unterschiedliche
Fingerprints führen zu einem Fehler; auf PostgreSQL wird die Prüfung durch eine
transaktionale Advisory-Sperre serialisiert
(`sensitive_meta_logic.py:85–177, 518–535`). Bestehende Integrationstests prüfen die
Zurückweisung sowohl vor als auch nach Löschung direkter Identifikatoren:
`tests/import_files/test_sensitive_identity_guards.py`,
`test_legacy_name_concatenation_collision_fails_without_partial_writes`.

Dieser Schutz darf nicht dem Hash selbst zugeschrieben werden. Der separate Dienst
`services/privacy/patient_pseudonyms.py:43–60` berechnet beispielsweise den Legacy-Hash
und speichert direkt am Patient-Modell, ohne die SensitiveMeta-Konfliktprüfung
aufzurufen. Dessen vollständiges Verhalten bei Konflikten ist hier nicht dynamisch
geprüft; eine repositoryweite Garantie für alle Schreibwege wäre daher unbelegt.

### 3 Hohe fachliche Relevanz identischer demografischer Daten

Zwei reale Personen mit gleichem Vor- und Nachnamen, Geburtsdatum und Zentrum
erhalten unter demselben Salt sowohl denselben Patientenhash als auch denselben
Identitätsfingerprint. Keine größere Hashlänge behebt fehlende Unterscheidungsdaten.
Die Wahrscheinlichkeit solcher Homonyme benötigt eine reale Verteilung der
Identitätsmerkmale; sie lässt sich weder aus 256 Bit noch aus synthetischen Tests
ableiten. Auch mehrere Untersuchungen desselben Patienten am selben Datum werden
durch die beschriebene Untersuchungshash-Formel nicht auseinandergehalten.

### 4 Mittlere Relevanz abweichender Schreibweisen und Verknüpfbarkeit

Groß-/Kleinschreibung, Unicode-Darstellung und innere Leerzeichen werden vom
Hashhelper nicht vereinheitlicht. Dadurch kann dieselbe Person mehrere Hashes
erhalten. Äußere Leerzeichen werden in einem vorgelagerten Serializerpfad entfernt,
nicht allgemein im Hashhelper. Die vorhandene
[Untersuchung der Eingabevarianten](patient_hash_input_variations.yml) und
`tests/services/test_patient_identity_input_variations.py` dokumentieren diese Grenze.

Gleiche Eingaben bleiben mit gleichem Schlüssel verknüpfbar. Der Zentrumsname im
Legacy-Hash begrenzt direkte Übereinstimmung zwischen unterschiedlich benannten
Zentren, macht eine Umbenennung aber identitätsrelevant. Der HMAC-Fingerprint nutzt
stattdessen die numerische Zentrums-ID. Das ist kein allgemeiner Schutz gegen
Verknüpfung über Zusatzwissen. Solange direkte Identifikatoren noch gespeichert
sind, kann ein berechtigter Datenbankzugriff außerdem die Zuordnung direkt lesen;
eine Hashinversion ist dafür unnötig.

## Quantitative Kollisionswahrscheinlichkeit

SHA-256 hat 256 Ausgabebits. Die Sicherheitsbetrachtung muss unterschiedliche
Hashnachrichten voraussetzen; demografische Gleichheit und identische Kodierungen
sind davon ausgeschlossen. [NIST FIPS 180-4](https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf)

Unter dem Modell unabhängiger, gleichverteilter 256-Bit-Ausgaben gilt für mindestens
eine Kollision unter `n` unterschiedlichen Nachrichten:

```text
p = 1 - Produkt(k = 0 … n-1, 1 - k / 2^256)
p ≈ 1 - exp(-n(n-1) / (2 × 2^256))
p ≈ n(n-1) / (2 × 2^256)   für sehr kleines p
```

| Unterschiedliche Nachrichten | Einzelner idealisierter 256-Bit-Hash | Zwei idealisierte Hashstufen, näherungsweise Obergrenze |
| ---------------------------- | ------------------------------------ | ------------------------------------------------------- |
| 1 Million                    | 4,318 × 10⁻⁶⁶                        | 8,636 × 10⁻⁶⁶                                           |
| 1 Milliarde                  | 4,318 × 10⁻⁶⁰                        | 8,636 × 10⁻⁶⁰                                           |
| 1 Billion                    | 4,318 × 10⁻⁵⁴                        | 8,636 × 10⁻⁵⁴                                           |

Die zweite Spalte gilt unmittelbar nur für eine idealisierte Stufe. Beim konkreten
Doppelhash können verschiedene Nachrichten bereits innen kollidieren oder
verschiedene innere Digests außen kollidieren. Eine Vereinigungsschranke im Modell
zweier idealer Stufen ergibt etwa den doppelten kleinen Wert. Beide Stufen nutzen
hier dieselbe Funktion; dies ist eine Modellrechnung, kein Unabhängigkeitsbeweis
für die Implementierung. Doppelhashing liefert keine verdoppelte Sicherheitsstärke.
Die 50-Prozent-Grenze eines einzelnen idealen 256-Bit-Hashs liegt bei rund
`4,01 × 10^38` Nachrichten. Das ist kein praktischer Patientenschwellenwert.

Bei einer Suche nach einer bestimmten Identität ist dagegen die Kandidatenmenge
entscheidend. Für `K` gleichwahrscheinliche, bekannte Kandidaten und bekannten
Salt wären im Mittel `(K+1)/2` Vergleiche bis zum Treffer nötig. Namen und
Geburtsdaten sind real nicht gleichverteilt. Eine Angabe zur Reidentifikationsrate
oder zu Sekunden pro Patient wäre ohne entsprechendes Experiment unbegründet.

## Kontrollierte Versuche und Reproduktion

Die Tests in `tests/utils/test_sensitive_meta_hash_security.py` rufen den tatsächlichen
Modellrechner und die zentralen Hashhelper auf. Sie verwenden ungespeicherte Modelle,
öffentliche Testwerte und einen expliziten Experiment-Salt; keine Patientendatenbank
und kein produktives Schlüsselmaterial werden als Versuchsquelle gelesen.

| Versuch                               | Prüfaussage                                                                                                         |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Wiederholung und Salt-Wechsel         | Identische Eingabe reproduziert den Hash; veränderter Salt ändert ihn im geprüften Fall                             |
| Sieben Feldaufteilungen               | Sieben gleiche Legacy-Hashes; alle sechs alternativen Aufteilungen haben einen anderen Fingerprint als die Referenz |
| 366 Geburtsdaten                      | Alle 366 Hashes sind in dieser Reihe verschieden                                                                    |
| Bekannter Salt und Ziel im Datumsraum | Genau ein kompatibles Geburtsdatum wird aus 366 Kandidaten gefunden                                                 |
| Drei kurze Salt-Kandidaten            | Der Ziel-Salt wird allein anhand des Salt-Fingerprints erkannt                                                      |

Das sind elf Testfälle, keine elf unabhängigen klinischen Stichproben. Ein endlicher
Versuch ohne zufällige Kollision beweist weder Kollisionsfreiheit noch eine
populationsbezogene Fehlerrate. Die künstliche Namensaufteilung ist absichtlich
adversarial; sie schätzt nicht deren Häufigkeit in realen Daten. Die Datumsreihe
ist deterministisch und nicht repräsentativ für eine Patientenpopulation.

Ausführung am 16.10.2026: **11 bestanden**, eine bestehende Pytest-Plugin-Warnung,
0,11 Sekunden laut Pytest (keine Benchmarkmessung). Vollständiger Pyright-Lauf:
0 Fehler, 24 Warnungen außerhalb der neuen Testdatei. Ruff erfolgreich; 65
Strukturmanifeste und 37 Feature-Definitionen gültig. Der Aufruf erfolgte mit den
bereits installierten Nix-Bibliothekspfaden für PostgreSQL und GCC sowie Binutils
im `PATH`, entsprechend der lokalen Laufzeitvoraussetzung aus dem Vorlagenthread.
Die reproduzierbaren Befunde beruhen auf den Testassertionen, nicht auf Laufzeitwerten.

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest tests/utils/test_sensitive_meta_hash_security.py -q
.devenv/state/venv/bin/python scripts/lint_structure.py
./feature-tracking/tracker.py validate
```

Für Datenbank- und Parallelitätsaussagen müssen zusätzlich die vorhandenen
Identitäts-, Salt-Rotations- und PostgreSQL-Parallelitätstests in einer eingerichteten
Testumgebung ausgeführt werden. Der Review ersetzt keine solche Integrationsprüfung.

## Folgerungen für die Architektur

Der bestehende versionierte HMAC mit getrennter Feldkodierung ist die geeignetere
Grundlage für einen künftigen Identitätsschlüssel. Ein Wechsel benötigt eine explizite
Migration bestehender Beziehungen und der Salt-Generationen; einfaches Ersetzen des
Legacy-Hashhelpers würde gespeicherte Identitäten verändern. Starke, zufällig
provisionierte Schlüssel und minimale Zugriffsmöglichkeiten auf Hashes,
Fingerprints und Berechnungsfunktionen begrenzen Kandidatenangriffe. Ein unbekanntes
Geheimnis darf dabei nicht durch die Bezeichnung „Salt“ als öffentlich behandelt werden.

Alle identitätserzeugenden Schreibwege sollten dieselbe Konfliktprüfung verwenden.
Demografische Gleichheit bleibt ein Zuordnungshinweis und benötigt bei Mehrdeutigkeit
einen fachlichen Prüfweg oder eine verifizierte externe Patientenkennung. Ungeprüfte
Normalisierung oder automatische Zusammenführung wäre keine sichere Korrektur.

Publizierbare Kernaussage: Die untersuchte Implementierung erzeugt reproduzierbare
Pseudonyme, zeigt konstruktiv nachweisbare Kollisionen ihrer Legacy-Feldkodierung
und erlaubt bei bekanntem Salt eine Wiedererkennung innerhalb vorgegebener
Kandidatenmengen. Ihre theoretisch geringe kryptografische Kollisionswahrscheinlichkeit
ist von diesen fachlichen und kodierungsbedingten Risiken strikt zu unterscheiden.
