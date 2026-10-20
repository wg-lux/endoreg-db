# Privater Patientenabgleich: Ergebnis der Lean-Formalisierung

Stand: 19. Oktober 2026. Grundlage ist die vom Nutzer bereitgestellte Bewertung
„Ansatz A: privater Vergleich bestehender Patienten-HMACs“.

## Ergebnis

Ein System für **autorisierten privaten Vergleich** ist unter geeigneten
kryptografischen und betrieblichen Annahmen realisierbar. Die stärkere Forderung,
gleichzeitig gezielt zu verknüpfen und jede Identifizierung gegenüber jedem
Beteiligten unmöglich zu machen, ist mit einer korrekt offengelegten
Verknüpfung unvereinbar.

Dieses Projekt liefert eine ausführbare abstrakte Spezifikation und **48 mit
Lean 4.19.0 überprüfte benannte Sätze**. Es beweist funktionale Eigenschaften,
bedingte Sicherheitseigenschaften eines Annahmeworkflows, einen bedingten
Kompositionssatz für Privatsphäre und mehrere Grenzen des gewünschten Systems.
Es liefert keine bereits sichere 2PC-Implementierung und keine Betriebsfreigabe.

Die entscheidende verbleibende Aufgabe ist die nachweisbare Verbindung zwischen
den tatsächlichen Eingaben, dem ausgeführten kryptografischen Backend und dem
hier spezifizierten Ergebnis. Signaturen und gleichlautende Referenzen allein
stellen diese Verbindung nicht her.

## Prüfung reproduzieren

Das Projekt verwendet ausschließlich die mit Lean gelieferte Bibliothek `Std`.
Mathlib und zusätzliche Lean-Pakete werden nicht benötigt. Die Toolchain ist
fest auf `leanprover/lean4:v4.19.0` gesetzt; dies ist der geprüfte Versionsstand,
keine Behauptung über die neueste verfügbare Lean-Version.

Mit installiertem Lean/Lake und Python 3.10 oder neuer, im Projektverzeichnis:

```bash
python3 verify.py
```

Bei einer lokalen Lean-Distribution außerhalb des PATH:

```bash
python3 verify.py --lake /pfad/zur/lean-distribution/bin/lake
```

Der Prüfer vergleicht die modulare und die zusammengefasste Quelle, baut das
Projekt, prüft die zusammengefasste Datei mit als Fehler behandelten Warnungen
und kontrolliert die transitiven Axiomabhängigkeiten aller benannten Sätze.

Der eigenständig prüfbare Quelltext benötigt nur Lean:

```bash
lean -DwarningAsError=true PatientMatchingProofs.lean
```

Für eine andere Arbeitsumgebung lässt sich die explizit gepinnte Version auch
mit Elan wählen:

```bash
lean +leanprover/lean4:v4.19.0 -DwarningAsError=true PatientMatchingProofs.lean
```

Nach Änderungen an den modularen Quellen wird die zusammengefasste Datei
ausdrücklich neu erstellt und erneut geprüft:

```bash
python3 make_standalone.py
python3 verify.py
```

Der ausgeführte abschließende Prüflauf meldete:

```text
PASS: 48 named theorems audited; no custom or unapproved axioms.
```

`validation.log` enthält die Kommandos, Exitcodes, Axiomlisten und Beispielausgaben.
`verification_summary.json` enthält den Versionsstand, alle Satznamen und
SHA-256-Digests der geprüften Quellen. Die beobachteten logischen
Standardaxiome sind `propext`, `Classical.choice` und `Quot.sound`. Es gibt keine
eigenen Axiomdeklarationen und keine offenen Beweise. Die Prüfung verwendet
keine Beweisergebnisse aus `native_decide` und keinen `unsafe`-Code.

Hypothesen eines Satzes bleiben Hypothesen, auch wenn seine Axiomliste keine
anwendungsspezifischen Axiome enthält. Insbesondere wurden die benötigte
Simulator-Eigenschaft, die Echtheit der Eingangsdaten und die Korrespondenz zum
kryptografischen Backend nicht durch eine Axiomprüfung bewiesen.

## 1. Welche Funktion formalisiert ist

A verfügt über einen vorhandenen Zielwert `t_A`, seinen passenden Schlüssel
`K_A`, seinen Zentrumsschlüssel `c_A` und seine vorhandene UUID. B verfügt über
die genehmigten Kandidatendaten und deren UUIDs. Für jeden Kandidaten gilt:

\[
b_i = [\operatorname{HMAC\!\text{-}\!SHA256}(K_A,
\operatorname{EncodePatient}(d_i,c_A)) = t_A].
\]

Der Vergleich verwendet A's Schlüssel und A's Zentrum. B's eigener HMAC und
B's eigener Schlüssel werden für diese Funktion nicht gebraucht. Der
ausführbare `Source`-Typ verlangt nicht, dass A die ursprüngliche Demografie
weiterhin speichert. Wo ein Beweis die ursprüngliche Eingabe benötigt, erscheint
sie als ausdrücklicher logischer Zeuge für die Bindung des gespeicherten Tags.

In Lean sind `mac` und `encode` **Funktionsparameter**. Die Sätze gelten für jede
entsprechende Funktion. Es wurde weder SHA-256 implementiert noch die exakte
Python-JSON-/UTF-8-Kodierung in Lean nachgebildet und mit Python bewiesen.

Das ist eine bewusste Beweisgrenze: Aussagen, die schon für jede Funktion gelten,
lassen sich überprüfen, ohne die kryptografischen Eigenschaften zu erfinden.
Eine sichere Implementierung muss die Parameter nachweisbar mit den richtigen
Produktionsfunktionen verbinden.

## 2. Bewiesene funktionale Eigenschaften

| Eigenschaft | Satz in `PatientMatching.Core` | Geltungsbereich |
|---|---|---|
| Gleich kodierter Kandidat reproduziert korrekt gebundenen Zielwert | `equal_payload_matches` | Derselbe Quellschlüssel und dasselbe Quellzentrum |
| Ein Treffer bedeutet gleiche Eingabebytes oder eine Kollision | `match_implies_equal_payload_or_collision` | Keine globale Kollisionsfreiheit unterstellt |
| Ohne die betreffende Kollision folgt Eingabegleichheit | `payload_equality_of_match_and_no_collision` | Ausschluss dieser Kollision ist eine explizite Voraussetzung |
| Eine UUID-Substitution verändert den HMAC-Vergleich nicht | `candidate_uuid_substitution`, `source_uuid_substitution` | UUIDs gehören nicht zur verglichenen Demografie |
| Ein bloß anderes Epoch-Label verändert den Vergleich nicht | `epoch_label_substitution` | Das Label authentifiziert den eingespeisten Schlüssel nicht |
| Trefferliste enthält genau die UUIDs passender Kandidaten | `matchingIds_membership` | Übergebene Kandidatenliste |
| Null, ein oder mehrere Treffer werden korrekt unterschieden | `classify_noMatch_iff`, `classify_singleMatch_iff`, `classify_ambiguous_iff` | Gezählt werden Listeneinträge/Records, nicht unterschiedliche Demografien |
| Ein unvollständiger Lauf ist ein Abbruch | `incomplete_is_abort` | Ergebnis der idealen Batchfunktion |
| Fehlende Generation wird als Fehler behandelt | `missing_epoch_is_error` | Keine Umdeutung zum Nonmatch |
| Ein vollständiger Einzelkandidat mit passender Eingabe ergibt dessen UUID | `single_candidate_completeness` | Zielwertbindung und gleiche Kodierung vorausgesetzt |

`run_noMatch_iff`, `run_singleMatch_iff` und `run_ambiguous_iff` verbinden die
Fallunterscheidung mit Vollständigkeit und verfügbarer Generation. Ein Nonmatch
sagt nur etwas über genau die ausgewertete Liste unter diesen Voraussetzungen.

## 3. Warum der demografische Hash keine Person eindeutig beweist

Der Satz `demographic_identifier_impossible` beweist für jede deterministische
Fingerprintfunktion:

\[
\bigl(\exists p\ne q:\ E(p)=E(q)\bigr)
\Longrightarrow
\neg\bigl(\forall p,q:\ F(E(p))=F(E(q))\Rightarrow p=q\bigr).
\]

Wenn zwei verschiedene Personen dieselbe kodierte Demografie besitzen, liefert
jede deterministische Fingerprintfunktion auf dieser Eingabe denselben Wert.
Hier entsteht keine kryptografische Hashkollision: Schon die Eingabe ist gleich.

`concrete_homonyms` liefert dafür ein widerspruchsfreies Zwei-Personen-Modell mit
unterschiedlicher Personenidentität und gleicher Demografie. Stärkere Schlüssel,
zusätzliche Verschlüsselung oder ein anderes Transportprotokoll ergänzen diese
fehlende Information nicht.

Daraus folgt nicht, dass eine Verknüpfung nutzlos wäre. Ihr verifizierbarer
Informationsgehalt muss zutreffend bezeichnet werden: ein passender vorhandener
Record beziehungsweise eine gleiche kodierte Demografie; der Bezug zur gleichen
realen Person erfordert zusätzliche Evidenz.

## 4. Ein realisierbares Gegenbeispiel zur Ausgabe aller Trefferbits

Der Anhang berichtet, dass der Versuch alle einzelnen Trefferbits beiden
Parteien ausgibt. Im vorgesehenen Ablauf erhält B dagegen zunächst nur die
zusammengefasste Entscheidung; A erhält zunächst Abschluss oder Abbruch.

`PatientMatching.Counterexamples.realizable_match_vectors` betrachtet einen
festen Kandidatensatz bei B:

| UUID | Demografische Eingabe |
|---|---|
| 1 | x |
| 2 | x |
| 3 | y |
| 4 | y |

Unter der expliziten Voraussetzung, dass x und y unter A's festem Schlüssel
verschiedene Tags haben, entstehen zwei mögliche Welten:

| Privater Zielwert bei A | Einzelne Trefferbits | Zusammengefasstes Ergebnis |
|---|---|---|
| Tag von x | `[true, true, false, false]` | `ambiguousMatches` |
| Tag von y | `[false, false, true, true]` | `ambiguousMatches` |

B's vollständige Eingaben bleiben unverändert; auch A's Schlüssel sowie die
modellierten öffentlichen Felder UUID, Zentrum und Epoch sind gleich.
`realizable_aggregate_disclosure` beweist die gleiche Zusammenfassung und die
unterschiedlichen Vektoren. `fixed_batch_has_uniform_shape` ergänzt die
ausdrückliche Voraussetzung gleicher öffentlicher Eingabelängen beziehungsweise
gleicher Form, falls das konkrete Backend diese veröffentlicht.

Die Ausgabe des Vektors verrät hier, welche Eingabegruppe passt. Die
zusammengefasste Mehrdeutigkeitsentscheidung verrät das nicht. Der Zusatzbefund
ist ein Problem der gewählten Ausgabe, auch bei fehlerfreier Kryptografie.

Die Modellierung umfasst die genannten öffentlichen Felder. Für ein vollständiges
reales Spiel müssen weitere Metadaten, Berechtigungsbelege, Seitenkanäle und
Abbruchinformationen ausdrücklich berücksichtigt werden.

## 5. Was der positive Privatsphärensatz genau aussagt

`PatientMatching.Privacy` verwendet einen endlichen, gleichverteilten
Zufallsraum `Fin N`. Ein Beobachter entscheidet anhand seiner Sicht mit einer
Booleschen Funktion, ob er die betrachtete Welt erkennt. Lean zählt, für wie
viele Zufallswerte dieser Beobachter `true` ausgibt.

Die Prämisse `SimulationBound` verlangt einen Simulator, dessen einzige
Eingaben der eigene Input der beobachtenden Partei und die ausdrücklich
erlaubte Information sind. Für jeden Beobachter dürfen die Akzeptanzzahlen
von realer und simulierter Sicht um höchstens e voneinander abweichen.

Der bewiesene Satz `simulation_implies_same_leakage_bound` lautet:

> Haben zwei Welten dieselben eigenen Eingaben und dieselbe erlaubte Information,
> dann unterscheiden sich ihre realen Akzeptanzzahlen um höchstens 2e.

Für N > 0 ergibt die Normalisierung eine Wahrscheinlichkeitsdifferenz von
höchstens 2e/N. Lean prüft die exakte ganzzahlige Schranke; die Darstellung als
Wahrscheinlichkeit ist deren mathematische Interpretation.

**Die Existenz dieses Simulators ist eine offene kryptografische Verpflichtung.**
Sie ist nicht bewiesen, indem die Prämisse einen Namen erhält. Die aktuelle
Definition quantifiziert über alle Beobachter und ist damit eine endliche
statistische Bedingung. Eine übliche rechenzeitbeschränkte Sicherheitsanalyse
von HMAC oder 2PC erfüllt diese stärkere Bedingung nicht automatisch. Für die
Anwendung muss eine passende formale Instanziierung beziehungsweise ein
entsprechendes Modell effizienter Angreifer ergänzt werden.

Der negative Satz `exact_membership_cannot_be_perfectly_hidden` zeigt:
Ein für jeden Zufallswert korrekt lesbarer Trefferbit unterscheidet eine
Trefferwelt von einer Nichttrefferwelt. Soll ein Simulator dieses bereits
offengelegte Bit dennoch nicht kennen, erzwingt
`hiding_disclosed_membership_requires_large_error` die Schranke N ≤ 2e.
Für N > 0 ist also mindestens ein Fehler von 1/2 je Welt notwendig.

Ein sinnvoller Schutzvertrag umfasst daher die zugelassene Verknüpfung als
erlaubte Information und schützt die übrigen Eingaben und Beobachtungen.
Der Satz über exakte Ausgaben setzt erfolgreiche korrekte Ausgabe für jeden
Zufallswert voraus; er wird nicht stillschweigend auf beliebige abbrechende
Ausführungen ausgedehnt.

## 6. Was die Annahmelogik für UUID-Beziehungen beweist

`PatientMatching.Workflow.accept` ist eine konkrete, reine Annahmefunktion.
Sie vergleicht Antwort und erwarteten Auftrag, prüft unveränderliche
Datensatzreferenzen durch Lookups, Kandidatenzugehörigkeit, Zentrum,
Schema/Epoch, verfügbare Epoch-Labels und bereits verwendete Sitzungskennungen.
Sie berücksichtigt außerdem das Resultat eines extern bereitgestellten
Signaturprüfers.

Nur eine als `.single candidate` deklarierte Antwort kann nach diesen Prüfungen
eine Beziehung anlegen. `non_single_rejected` schließt andere Resultattypen aus.
`accepted_rows_unchanged` und `existing_links_preserved` beweisen, dass die
Datensatz-Snapshots und bereits gespeicherte Beziehungen erhalten bleiben.
Es wird eine Kante angelegt, keine Zusammenführung der Patienten durchgeführt.

`replay_rejected` beweist, dass der nach einer Annahme zurückgegebene Zustand
die erneute Verwendung derselben Sitzung verhindert, auch bei einer geänderten
Anfrage oder Antwort. Eine Anwendung muss diesen Zustandsübergang atomar und
dauerhaft speichern. Gleichzeitige Zugriffe auf denselben alten Zustand,
Rollback, Uhrzeitprüfung und Crash-Recovery sind damit nicht bereits bewiesen.

Die Referenzen auf Fingerprints, Payloads, Batches und Circuits sind abstrakte
natürliche Zahlen. Es wird die Gleichheit solcher Referenzen geprüft, nicht ihre
Bindung an konkrete geheime Bytes oder an eine tatsächliche Circuit-Ausführung.
Auch die Zulässigkeit des erwarteten Auftrags und die Wahrheit der gelieferten
Snapshots sind Voraussetzungen außerhalb dieser Annahmefunktion.

Eine korrekte Signatur über eine falsche Trefferbehauptung genügt nicht als
Rechennachweis. Der Satz
`Counterexamples.accepted_single_is_unique_if_backend_refines` ergänzt deshalb
eine explizite Prämisse `backendRefinement`: Die deklarierte Antwort muss dem
Ergebnis der vollständigen Kernberechnung auf den angegebenen Eingaben
entsprechen. **Unter dieser Voraussetzung** folgt, dass genau die akzeptierte
UUID in der Trefferliste steht und Vollständigkeit sowie verfügbare Generation
gegeben sind. Die Verbindung der abstrakten Referenzen mit diesen Eingaben
bleibt zusätzlich nachzuweisen.

## 7. Abbruchschutz und Fairness

Die formalisierte fachliche Annahmefunktion übernimmt keinen als unvollständig
deklarierten Lauf. Das ist kein Beweis, dass ein bösartiger Teilnehmer niemals
ein Ergebnis erfährt und anschließend die Ausführung abbricht.

Insbesondere können bereits inkrementell ausgegebene Trefferbits nicht durch
spätere Fehlerbehandlung zurückgenommen werden. Der Circuit sollte das
gewünschte Gesamtergebnis intern berechnen und seine Ausgabe auf den vorgesehenen
Empfänger begrenzen. Auch dann bleiben Schutz mit Abbruch, Fairness und
garantierte Auslieferung verschiedene Anforderungen.

## 8. Konsequenz für den vorliegenden Entwicklungsstand

Die bereitgestellte Bewertung meldet 39 bestandene Referenztests und einen
ausgeführten MP-SPDZ/Tinier-Versuch. Diese Angaben wurden hier als Bericht
ausgewertet; die lokalen Repository-Arbeitsbäume, Rohmessungen und der `.mpc`-Code
wurden mit der Anlage nicht bereitgestellt und hier nicht unabhängig erneut
ausgeführt. Die neuen Lean-Prüfungen sind davon getrennte, tatsächlich
ausgeführte Evidenz.

Der Bericht nennt für 100 synthetische, wiederholte Kandidaten 171,81484 Sekunden
Wandzeit und 32.482,8 MB globale Backend-Kommunikation. Das entspricht ungefähr
32,5 GB in dezimaler Darstellung. Die Läufe sind keine kontrollierte klinische
Skalierungsreihe; insbesondere unterscheiden sich teils die Compilerparameter.
Diese Zahlen rechtfertigen keinen Schluss auf brauchbare Leistung im Klinik-WAN.

MP-SPDZ klassifiziert Tiny/Tinier als Protokolle mit Schutz gegen bösartige
Teilnehmer bei unehrlicher Mehrheit. Die Projektdokumentation stellt zugleich
klar, dass die implementierten Protokolle und Benchmarks keine zugesicherte
Sicherheitsprüfung für kritischen Produktivcode bedeuten. Welche Aussagen eines
publizierten Protokollbeweises exakt für die gepinnte Konfiguration gelten,
bleibt Gegenstand einer separaten Prüfung.

## 9. Nächster Implementierungsschritt

Eine begrenzte Fortsetzung von Ansatz A ist begründet. Die Beweisziele legen
folgende Reihenfolge nahe:

1. Den zugelassenen Informationsgewinn festlegen: wer sieht welchen Ausgang,
   welche UUID-Beziehung, welche Metadaten und welche Fehler?
2. Vollständige Aggregation und empfängerbezogene Ausgabe im Circuit umsetzen.
   Fachliche Zwischenresultate nicht vor Abschluss der nötigen Prüfungen
   veröffentlichen; gegenüber bösartigen Teilnehmern keine Fairness behaupten.
3. Private Eingaben nachweisbar an genehmigte, unveränderliche Records,
   Zentrum, Schema und passende Schlüsselgeneration binden. Syntaktisch gültige
   Daten und klinisch verifizierte Identität getrennt behandeln.
4. Exakte Produktionskodierung, HMAC-Schaltung und Backendausgabe mit dem
   Lean-Modell verbinden. Insbesondere die `backendRefinement`-Verpflichtung
   erfüllen, statt sie durch eine Signatur zu ersetzen.
5. Den Annahmeübergang mit atomarer Persistenz, Ablauf/Widerruf und Schutz gegen
   Zustandsrollback implementieren. Für neue, adaptive Abfragen sind separate
   Berechtigungen und kumulative Budgets erforderlich.
6. Die tatsächlichen Administrations-, Prozess-, Backup- und Recovery-Grenzen
   sowie die Transport- und Ausgabewege prüfen; dann die konkrete gepinnte
   kryptografische Implementierung unabhängig prüfen und realistisch messen.

Die Ablage von Hub und Vault auf demselben Host entscheidet die Sicherheit eines
direkten A/B-Protokolls nicht allein. Maßgeblich ist, wer auf die vollständigen
privaten Eingaben beider Endpunkte zugreifen kann. Zwei Rollen unter derselben
ausreichend privilegierten Administration ergeben keine unabhängige Grenze
gegenüber dieser Administration.

Ein verifiziertes privates Matchingprotokoll würde weiterhin nicht die Anonymität
aller übertragenen medizinischen Datensätze beweisen. Dateihashes, bekannte
Originaldateien, Exportmetadaten und klinische Inhalte gehören zu einem eigenen
Veröffentlichungs- und Angreifermodell.

## Quellen und Provenienz

- Nutzeranlage: `Pasted markdown(20261019-155405).md`, Bewertung vom 19.10.2026.
  SHA-256 der bereitgestellten Datei:
  `7dc06ee77860798973714f68724e6ebe5f272e0a7c5316f1b27c7bbd0c86d03a`.
- [RFC 2104: HMAC](https://www.rfc-editor.org/rfc/rfc2104.html).
- [MP-SPDZ: Getting Started, Preface und Protokollübersicht](https://mp-spdz.readthedocs.io/en/latest/readme.html), am 19.10.2026 eingesehen.
- [Lean: Axioms and Computation](https://lean-lang.org/theorem_proving_in_lean4/Axioms-and-Computation/), Grundlagen und transitive Axiomprüfung.
- [Lean installieren](https://lean-lang.org/install/).
- Verwendetes offizielles [Lean-4.19.0-Linux-Archiv](https://github.com/leanprover/lean4/releases/download/v4.19.0/lean-4.19.0-linux.tar.zst).
  SHA-256 des tatsächlich verwendeten Downloads:
  `6fe3ce97a58f44e2b3567d455b994eacec5bfe9ae7774f2a573444480ba813fe`.

Die Download-Prüfsumme dokumentiert die verwendeten Bytes. Sie ersetzt keine
separate Signaturprüfung der Toolchain. Der Lean-Kernel und die verwendete
Ausführungsumgebung bleiben Teil der Vertrauensbasis.

## Dateien

| Datei | Zweck |
|---|---|
| `PatientMatching/Core.lean` | Abstrakter Vergleich, Ergebnissemantik und grundlegende Gegenbeispiele |
| `PatientMatching/Privacy.lean` | Endliche Simulationsschranke und Unvereinbarkeit von sichtbarer und gleichzeitig verborgener Mitgliedschaft |
| `PatientMatching/Workflow.lean` | Konkrete Annahmelogik für Referenzen und Sitzungszustand |
| `PatientMatching/Counterexamples.lean` | Realisierbare Mehr-Offenlegung und bedingte Verbindung zum Backend |
| `PatientMatching.lean` | Modul-Einstiegspunkt |
| `PatientMatchingProofs.lean` | Vollständiger eigenständig prüfbarer Quelltext mit Axiomprüfung |
| `lean-toolchain`, `lakefile.toml`, `lake-manifest.json` | Reproduzierbare Projektkonfiguration |
| `make_standalone.py` | Zusammenfassung und Satzmanifest erstellen |
| `verify.py` | Build, Warnungsprüfung, Quellenvergleich und Axiomaudit |
| `proof_manifest.json` | Benannte Sätze |
| `validation.log`, `verification_summary.json` | Ergebnis des tatsächlich ausgeführten Prüflaufs |

