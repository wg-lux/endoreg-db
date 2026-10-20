# Ansatz A: privater Vergleich bestehender Patienten-HMACs

Bewertung vom 19. Oktober 2026. Umfang und Freigabestatus sind ausschließlich in
[HubTransfer.yml](../feature-tracking/HubTransfer.yml),
`existing_hmac_2pc_evaluation` und `cross_site_private_patient_matching`, geführt.
Dies ist eine isolierte Evaluation, keine Betriebsfreigabe oder Migration.

## A. Entscheidung

| Aussage | Entscheidung | Begründung |
|---|---|---|
| Kryptografische Berechenbarkeit | **supported** | HMAC-SHA256 und Gleichheit sind endliche Boolesche Schaltungen. Eine tatsächliche Zwei-Prozess-Auswertung mit MP-SPDZ/Tinier wurde ausgeführt. A muss seinen Schlüssel nicht an B weitergeben. |
| Exakter Tupelvergleich | **conditionally supported** | Referenztests stimmen mit der produktiven Kodierung überein. Der Versuchs-Schaltkreis verarbeitet vorserialisierte Bytes; er erzwingt nicht, dass ein bösartiges B gültige Patienten-JSON mit A als Zentrum eingibt. |
| Authentifizierte Beziehung bestehender UUIDs | **not established by current evidence** | Die Berechnung bindet weder Datenbankzeilen noch UUIDs oder Berechtigungen. Signierte Abgleichsergebnisse und ein persistiertes Beziehungsmodell fehlen. |
| Verifizierte Identität derselben realen Person | **not established by current evidence** | Gleiche Namen/Geburtstage können verschiedene Menschen bezeichnen; Namensänderungen oder Schreibweisen verhindern echte Treffer. Der verifizierte Ausstellervertrag bleibt unerfüllt. |
| Praktische Leistung | **conditionally supported** | Lokale synthetische Messungen belegen ausschließlich die unten ausgewiesenen Größen; keine Aussage über Klinik-WAN, Parallelbetrieb oder Produktionskapazität. |
| Betriebsbereitschaft | **not established by current evidence** | Unabhängige Zugriffsgrenzen, authentifizierte Eingabebindung, minimaler Ergebniskanal, Missbrauchsschutz, Integration und Sicherheitsprüfung fehlen. |

Die fünf auseinanderzuhaltenden Behauptungen sind: (1) die Bytes eines Kandidaten
erzeugen den Ziel-HMAC unter dem gewählten Schlüssel; (2) der Zielwert gehört zur
behaupteten Quell-UUID; (3) der Kandidat gehört zur behaupteten B-UUID;
(4) beide betreffen dieselbe reale Person; (5) niemand erfährt mehr als die
definierte Protokollleckage. Der Schaltkreis untersucht (1); daraus folgen
(2) bis (5) nicht automatisch. Eine Signatur bestätigt eine Aussage eines
Schlüsselinhabers, nicht die Wahrheit der Demografie. B erkennt einen bewusst
verknüpften Quelldatensatz, wenn es den eigenen Patienten bereits kennt.

## B. Aktuelle Befunde und Evidenz

Untersucht wurden die tatsächlichen Arbeitskopien, einschließlich vorhandener
Änderungen: `/home/admin/endoreg-db`, Branch `prototype`, Commit
`08c1ac0da110343dfc6d0314b24998a1937d9aeb`; `/home/admin/luxnix`, Branch
`lx-annotate_ollama`, Commit `cda962d31bda2811cecdeaeb413c6c84f8a0063e`.
Beide Arbeitsbäume waren bereits verändert. Es gab keinen Reset, Checkout,
Produktionszugriff, Geheimnisabruf oder Datenbankumbau. Die Belege beziehen sich
auf den geprüften Arbeitsbaum, nicht ausschließlich auf HEAD. LuxNix-Quellcode
belegt deklarierte Konfiguration, keinen ausgerollten Zustand.

Pfade ohne Präfix liegen in endoreg-db; `luxnix/` bezeichnet die zweite Arbeitskopie.
Zeilen sind eine Momentaufnahme und können durch parallele Änderungen wandern.

| Claim | Status | Code or deployment evidence | Assumptions | Remaining gap |
|---|---|---|---|---|
| Exakte v1-Kodierung und HMAC | implementiert | `endoreg_db/utils/hashs.py:75–118` | Schema ist wirklich v1 | Altbestände nicht pauschal v1 |
| Schlüsselstring bleibt UTF-8 | implementiert | `utils/hashs.py:31–55`, `utils/verify_salt.py:65–110` unter `endoreg_db/` | gültiges konfiguriertes Material | Stärkeprüfung beweist keine Zufallsentstehung |
| Öffentlicher Generationsfingerprint | implementiert | `endoreg_db/utils/hashs.py:70–72` | erwarteter Wert authentifiziert | kein Ausstellerbeweis; Offline-Test schwacher Schlüssel möglich |
| Historische Keys verfügbar | bedingt | `endoreg_db/config/secret_keyring.py:21–57,198–205`; `endoreg_db/services/secret_rotation/historical_resolution.py:53–63` | richtige lokal erhaltene Generation | keine individuellen Epochennummern im retiring-Tupel; Legacy-Doppel-SHA und HMAC koexistieren |
| Retiring nur lesend | nicht erzwungen | `endoreg_db/utils/hashs.py:38–42` | Aufrufer hält Policy ein | Primitive erlaubt beliebige neue HMAC-Auswertung |
| Datensatz/Hash-Verknüpfung | lokale Checks | `endoreg_db/models/metadata/sensitive_meta.py:145–157`; `models/administration/person/patient/patient_hash_alias.py:17–54` unter `endoreg_db/`; `services/secret_rotation/historical_resolution.py:82–134` | vertrauenswürdige lokale Persistenz | keine signierte Scheme-/Epoch-/UUID-Aussage; Felder teils leer |
| Zentrum eindeutig | nicht generell | `endoreg_db/models/administration/center/center.py:29–33,85–89,109–125` | kontrollierter Bestand | nicht unique; resolve_identity nimmt ersten Treffer; save-Schutz nicht DB-unumgehbar |
| B besitzt Originaldemografie | nur manche Quellen | `endoreg_db/models/metadata/sensitive_meta.py:128–135`; `services/privacy/quality_evaluation.py:783–880` unter `endoreg_db/` | erhaltene, autorisierte Originalwerte | Löschung und Pseudonymisierung verhindern Rekonstruktion aus lokalem HMAC |
| Patient-Modellwerte sind Originale | falsch als allgemeine Annahme | `endoreg_db/models/administration/person/patient/patient.py:119–138` | keine | Pseudonamen und randomisierter Geburtstag sind keine Abgleichseingaben |
| Hub kann B sein | mit aktuellen Transferdaten nicht belegt | `endoreg_db/services/hub/transfers.py:2151–2182` | zusätzliche autorisierte Quelle nötig | Hub legt leere Namen und kein Geburtsdatum an |
| Externe ID ist verifiziert | nicht belegt | `endoreg_db/models/administration/person/patient/patient_external_id.py:18–31` | Ausstellerprüfung hinzuzufügen | Herkunft/Wert ohne Verifikationsnachweis |
| UUID bleibt erhalten | implementiert | `endoreg_db/models/administration/person/patient/patient.py:178–180`; `services/hub/transfers.py:2090–2112` unter `endoreg_db/` | autorisierter Quellknoten | bestehende Transferbindung ist kein Cross-Site-Matching |
| Quellhash bleibt lokal | implementiert | `endoreg_db/services/privacy/response_boundary.py:14–55` | unveränderte Exportgrenzen | neuer Abgleich darf keine pauschale Freigabe schaffen |
| Replay-/Transportschutz existiert | für Transfer | `endoreg_db/services/hub/transfers.py:535–575,887–908`; `views/media/hub/transfers.py:231–280` unter `endoreg_db/` | aktives mTLS-Profil korrekt | keine Matching-Session und keine Grenzen frischer adaptiver Abfragen |
| Envelope bindet Metadaten | implementiert | `endoreg_db/services/hub/transfer_envelope.py:209–243` | legitime Sender | keine Signatur über ein tatsächliches Abgleichergebnis |
| Hub und Vault auf gs-02 | deklariert | `luxnix/ansible/inventory/host_vars/gs-02.yml:84,146` | Konfiguration ausgerollt | schließt direkten A/B-Abgleich nicht aus |
| Identity-Keys lokal provisioniert | deklariert | `luxnix/scripts/provision-identity-salts.py:186–203,233` | Dateirechte und Prozessisolation wirksam | Anwendung liest Schlüssel; Root liest Recovery |
| Unabhängige Administration | nicht belegt | `luxnix/ansible/inventory/hosts.ini:177–183`; `group_vars/hub_enrollment_admin_access.yml:2–5` | tatsächliche sudo/DB/Backup-Rechte prüfen | gemeinsame Controller-SSH-Identität vorgesehen |
| Recovery ist separate Vertrauensgrenze | nein | `luxnix/modules/nixos/services/lx-annotate-local/subservices/lx-annotate-identity-salts.nix:14–17,23,30–44` | keine | zwei Dateien desselben Hosts; Root-Service |

### Produktionsfunktion und historische Auswahl

A hält `uuid_A`, `t_A`, `K_A`, `c_A` und Herkunftsbelege. B hält eine begrenzte
Liste `(uuid_B, first_name, last_name, dob)` aus autorisierten Originaldatensätzen.
Die Funktion lautet exakt `HMAC-SHA256(K_A, EncodePatient(d_i,c_A)) == t_A`.
B's eigener Schlüssel und lokaler HMAC werden hierfür nicht gebraucht.
Eine gelöschte Demografie lässt sich daraus nicht zurückgewinnen.

Die JSON-Liste enthält `patient-identity-v1`, getrimmten Vor- und Nachnamen,
ISO-Datum und getrimmten Quellzentrumsschlüssel. `datetime.date()` übernimmt
das vorhandene Datum ohne Zeitzonenumrechnung. Python `strip()` entfernt auch
Unicode-Randwhitespace; keine Kleinschreibung, Akzentangleichung oder NFC/NFD-
Normalisierung. `ensure_ascii=False`, kompakte Separatoren und UTF-8 bleiben
unverändert; Anführungszeichen, Backslashes und Steuerzeichen folgen JSON.
Ungültige Unicode-Surrogate können nicht UTF-8-kodiert werden und müssen abbrechen.
Hex- oder Base64-artige Schlüsselstrings werden **nicht dekodiert**.

Die Formulierung „collision-free“ in `hashs.py:83` ist unzutreffend:
eindeutige Feldkodierung ist weder ein unendlicher kollisionsfreier Digest noch
ein eindeutiger Menschenschlüssel. HMAC verarbeitet lange Schlüssel durch
vorheriges Hashen und danach Blockpadding; gecachte innere/äußere Zustände
sind wie Schlüssel zu schützen. [RFC 2104](https://www.rfc-editor.org/rfc/rfc2104.html)

Der erwartete Generationsfingerprint ist
`HMAC-SHA256(K_A,b"endoreg-identity-salt-v1")`. Er muss aus einer authentifizierten
lokalen Record-/Issuer-Bindung kommen, nicht aus derselben unbeglaubigten Anfrage.
Ein In-Circuit-Vergleich prüft Schlüsselkonsistenz, aber nicht, ob `t_A` zu
`uuid_A` gehört. Veröffentlichte Fingerprints erlauben Offline-Tests geratener
schwacher Keys. Legacy-Hashes sind vor Auswahl des v1-Schaltkreises auszuschließen.
Retiring-Keys brauchen explizite erlaubte Zwecke, Ablauf und Widerruf;
Kommentare im Hashhelper reichen nicht. Caches dürfen keine unabhängigen,
ungeprüften HMAC-Zustände als Ersatz für den gebundenen Schlüssel einschleusen.

## C. Protokollentwurf und Vertrauensgrenzen — noch nicht integriert

Die zwei Endpunkte sind **A's lokaler Matching-Worker** und **B's lokaler
Matching-Worker**. Ein Hub kann Anfragen vermitteln, erhält aber weder `K_A`
noch `t_A` noch Kandidatentupel. Es wird kein zentraler gemeinsamer Matching-Key
benötigt. mTLS authentifiziert Endpunkte und schützt Kanäle; es ersetzt keine 2PC.
Ein Server, der beide Eingaben über TLS erhält, wäre keine private Zwei-Parteien-
Berechnung. Passwort-Hashes, API-Geheimnisse und Master-Keys werden nicht zu
asymmetrischen Schlüsseln umfunktioniert.

| Angreifer | Erwarteter Schutz / Grenze |
|---|---|
| Protokolltreuer neugieriger Teilnehmer | Nur festgelegte Ausgaben und öffentliche Metadaten, unter dem Backendmodell; keine fremden privaten Inputs. |
| Bösartiges A oder B | Aktive Protokollsicherheit erkennt kryptografisches Fehlverhalten oder bricht ab; sie verhindert keine frei erfundenen, formal gültigen Eingaben. Berechtigungsbindung bleibt zusätzlich nötig. |
| Neugieriger/kompromittierter Hub | Darf nur Vermittlungsmetadaten und später autorisierte UUID-Beziehung sehen; Netzwerktraffic braucht authentifizierte Verschlüsselung. |
| Root-/App-/DB-/Backup-Administratoren | Können lokale Inputs auslesen. Derselbe entsprechend privilegierte Betreiber beider Endpunkte sieht beide Inputs; 2PC beseitigt diese Rechte nicht. |
| Kollusion | Hub plus eine Partei besitzt deren Eingaben und erlaubte Ergebnisse; Kollusion beider Parteien hebt deren gegenseitige Vertraulichkeit auf. Gemeinsame Verwaltung darf nicht als Nichtkollusion gelten. |
| Spätere Kompromittierung | Erhaltene Schlüssel plus Zieltoken erlauben Offline-Kandidatenprüfung. Logs, Crashdumps, Swap, Backups und Preprocessing-/Cachezustand gehören zur Grenze; kein pauschales Forward-Secrecy-Versprechen. |

Vault schützt nicht unabhängig vor dem Host, der seine laufenden Geheimnisse
verarbeitet; das dokumentierte Modell unterscheidet untrusted Storage und
autorisierte Laufzeit-/Policy-Zugriffe. [Vault security model](https://developer.hashicorp.com/vault/docs/internals/security)
Die LuxNix-SSH-Gruppe beweist allein keine Root-Rechte: siehe
`luxnix/docs/vault-hub-machine-enrollment.md:1063–1066`. Tatsächliche Root-,
App-, DB-, Deployment-, Backup- und Recovery-Rechte beider Kliniken müssen
inventarisiert werden. Die bisherige Forderung nach separat geschützten Keys
wird nicht aufgehoben, sondern auf die tatsächlichen A/B-Rollen angewandt.

### Eingaben, Nachrichten und Bindungen

1. A autorisiert genau einen bestehenden Datensatz und erstellt einen signierten,
   kurzlebigen Session-Auftrag: zufällige Request-ID, A/B-Knoten und Issuer,
   Publikum, Zweck, Schema, Schaltkreisversion/-digest, Epoch-Referenz, Ablauf
   und autorisierte Quell-UUID. Zieltoken bleibt im privaten A-Input.
   Der lokale Autorisierungsdienst bindet `t_A ↔ uuid_A ↔ c_A ↔ scheme ↔ epoch`
   an die ausgewählte unveränderliche Datensatzversion. Ein bloßes vom Worker
   frei wählbares Feld ist hierfür kein Beweis.
2. B erstellt aus zulässigen, tatsächlich vorhandenen Originalen einen
   unveränderlichen Kandidatensatz mit lokalen UUIDs und Versionsbelegen.
   Ein kontrollierter lokaler Adapter bindet Tupel und UUID; gegenüber einem
   bösartigen Standort erfordert dies eine zusätzliche vertrauenswürdige
   Aussteller-/Attestierungsgrenze oder ein geprüftes Credential-Verfahren.
   Frei erfundene Kandidaten dürfen nicht als verifiziert erscheinen.
3. Beide prüfen Session, Zertifikate, Zweck, erlaubte Keygeneration, Mengenlimit,
   Ablauf und identischen Circuit. Sie führen den geprüften 2PC aus. Serialisierung
   außerhalb der Schaltung ist für ehrliche Adapter korrekt; gegenüber bösartigem
   B muss die Schaltung das Format und `c_A` erzwingen oder die Eingabe muss
   nachweisbar an einen geprüften Serializer gebunden sein. Das ist noch offen.
4. Minimaler fachlicher Output: B erhält nur `no_match`, `single_match` mit lokalem
   Treffer oder `ambiguous_matches`; A zunächst lediglich Abschluss/Abbruch.
   Erst nach autorisierter Freigabe eines eindeutigen Treffers erhalten A und Hub
   die notwendige UUID-Beziehung. Für Ambiguität bleibt Kandidatenprüfung bei B.
   Der aktuelle Versuch gibt stattdessen alle Matchbits beiden Parteien aus:
   ausdrücklich mehr Leckage als dieses Ziel, kein fertiger Minimaloutput.
5. Beide Parteien signieren dieselbe Ergebnisstruktur einschließlich Request-ID,
   Teilnehmern, Publikum, Zweck, Schema, Epoch, Circuit-Digest, Kandidatensatz-
   Referenz, Ergebnis, relevanten UUIDs und Ablauf. Signaturschlüssel sind separat
   provisionierte asymmetrische Schlüssel. Nur nach erfolgreichem Backend-Abschluss
   darf der vertrauenswürdige Wrapper signieren. Zwei Signaturen sind kein
   allgemein öffentlich prüfbarer Beweis der tatsächlichen Berechnung.
6. Der Empfänger prüft Signaturen, Widerruf, Ablauf, Session- und Audience-Bindung
   und atomare Einmalverarbeitung. Er speichert eine explizite, widerrufbare
   Beziehung `(issuer_A, uuid_A, issuer_B, uuid_B, evidence, status)` mit Auditspur.
   UUIDs und lokale Hashes ändern sich nicht; es gibt keine automatische Zusammenführung.

Im Zielmodell kennt nur A `t_A` und `K_A`, nur B die Tupel. Beide kennen Session,
Schema, zugelassene Größenklassen, öffentlich vereinbarte Key-/Zentrum-Metadaten
und Verbindungsdauer. Eine öffentlich sichtbare Epoch-Referenz erlaubt deren
Wiedererkennung; sie darf nur veröffentlicht werden, wenn dies freigegeben ist.
Hub sieht Routing, Zeiten und freigegebene Beziehungen. Größenpadding kann
Kandidatenzahl und Bytelängen teilweise verbergen, kostet aber zusätzliche
Schaltungen; Dummy-Kandidaten müssen als solche intern maskiert werden.
Fehler werden extern grob klassifiziert; sensible Detailfehler bleiben lokal.

### Ergebnissemantik und wiederholte Abfragen

| Ergebnis | Bedeutung |
|---|---|
| `no_match` | Vollständige Auswertung genau des genehmigten Kandidatensatzes unter gewähltem Schema/Zentrum/Key ohne Treffer; keine globale Nichtexistenz. |
| `single_match` | Genau ein passendes lokales Record im vollständigen Satz, keine automatisch verifizierte Person. |
| `ambiguous_matches` | Mehrere Records passen, auch bei identischer Demografie verschiedener UUIDs; keine automatische Auswahl. |
| `aborted_or_incomplete` | Timeout, Manipulation, fehlende Inputs, inkonsistente Bindung, Abbruch oder unvollständiger Satz; niemals Nonmatch. |
| `key_epoch_unavailable` | Ausgewählte zugelassene Generation fehlt; kein Ausweichen auf aktive/fremde Generation. |

Replay-Schutz verhindert nicht Mitgliedschaftstests mit neuen Request-IDs,
überlappenden Batches oder adaptiv ausgewählten Quell-UUIDs. Nötig sind
patienten-/zweck-/standortübergreifende Abfragebudgets, geprüfte Kandidatensätze,
Konsistenz bei Wiederholung, Audit und fachliche Zugriffskontrolle. Ein
unbegrenztes autorisiertes Mitgliedschaftsorakel bleibt ein Identifizierungsrisiko.
Ein abgebrochener Lauf darf keine verwertbaren Teilresultate publizieren.

## Implementierungsvergleich: höchstens zwei Backends

**MP-SPDZ v0.4.4**, Commit `113c4e47dd94bca9ecab029d2f0abe649bea5c52`,
ist der ausgeführte Kandidat. Boolesche SHA-256-Schaltungen erlauben die
HMAC-Komposition und Gleichheit; private Bits gehören je einer Partei.
Tinier ist als malicious/dishonest-majority klassifiziert; mit zwei Parteien
ist eine korrupte Partei vorgesehen, keine Fairness oder garantierte Ausgabe.
Die Software bezeichnet sich als Benchmarkframework ohne zugesicherte
Produktions-Sicherheitsprüfung. [Dokumentation](https://mp-spdz.readthedocs.io/en/latest/readme.html)
Die Frameworkbeschreibung ist [Keller 2020](https://eprint.iacr.org/2020/521).

Tinier nutzt authentifiziertes Secret Sharing und OT-basierte Vorverarbeitung
nach [FKOS15](https://eprint.iacr.org/2015/901), mit der im
[gepinnten Quelltext](https://github.com/data61/MP-SPDZ/tree/113c4e47dd94bca9ecab029d2f0abe649bea5c52)
beschriebenen Cut-and-Choose-Anpassung nach
[Furukawa et al.](https://eprint.iacr.org/2016/944).
OT-/Symmetrieannahmen, korrekte Zufallsquellen, frisches geprüftes Preprocessing
und nicht gemeinsam kompromittierte Endpunkte sind Voraussetzungen. Die
Komposition aller Implementierungsoptimierungen mit diesen Beweisen wurde hier
nicht unabhängig bewiesen oder auditiert. MAC-/Protokollfehler müssen abbrechen;
fachlich falsche, aber syntaktisch akzeptierte Inputs bleiben möglich.
Der FKOS15-Volltextabruf über ePrint und das IACR-Proceedings-Archiv scheiterte
mit HTTP 403; zugänglich waren die Primärzusammenfassung, Quellenverweise und
Implementierung. Eine vollständige Prüfung der publizierten Beweisannahmen ist
daher ausdrücklich **nicht abgeschlossen**. Insbesondere ist die übernommene
Cut-and-Choose-Analyse kein pauschaler Beweis eines Zwei-Parteien-Protokolls:
die zitierte Furukawa-Arbeit behandelt als Gesamtprotokoll drei Parteien.
BSD-3-Clause für den Hauptcode, zusätzliche Drittkomponentenlizenzen in
`License.txt`. Aktuelle Distribution und umfangreicher Compiler, C++-Runtime,
OT-, OpenSSL- und CPU-Abhängigkeiten bedeuten erheblichen Integrationsaufwand;
kein vorhandener Django-Adapter oder Nix-Deploymentnachweis.

**EMP ag2pc**, Commit `a245ca0f67abaf2d48c711e3c050ce961e60ad29`, unterstützt
Boolesche Schaltungen, private Teilnehmerinputs und empfängerbezogene Ausgabe.
Das aktuelle API beschreibt aktive Sicherheit mittels authentifiziertem Garbling,
OT-Konsistenzchecks vor Ausgabe und Fehlern bei ungültiger Wire-Lebensdauer.
SHA-256/HMAC ist daraus konstruierbar, hier aber nicht ausgeführt. Der aktuelle
Rewrite ist ausdrücklich nicht auditiert; Apache-2.0, C++20, CMake, OpenSSL,
emp-tool/emp-ot und eigener Adapter erforderlich.
[Aktueller Primärquelltext](https://github.com/emp-toolkit/emp-ag2pc/tree/a245ca0f67abaf2d48c711e3c050ce961e60ad29)
Die referenzierte Sicherheitsanalyse ist
[KRRW18](https://eprint.iacr.org/2018/578); ihre Annahmen zu authentifiziertem
Garbling/OT und aktiver Sicherheit ersetzen keine Prüfung des neuen Codes.
Auch hier: zwei getrennte Parteien, Authentisierung der Verbindungen,
Abbruch statt verlässlicher Verfügbarkeit und keine automatische Eingabeechtheit.
Kein zweiter Benchmark und keine Behauptung eines fertigen HMAC-Adapters.

[RFC 9497](https://www.rfc-editor.org/rfc/rfc9497.html) spezifiziert eine andere
OPRF/VOPRF-Funktion. Ihre Auswertung reproduziert den bestehenden HMAC nicht;
ein Wechsel wäre ein eigener Entwurf mit Migration. Die vorhandene begrenzte
Kandidatensuche `scripts/benchmark_sha256.py:20–24,47–99` fixiert den Schlüssel
und enumeriert Nachrichten. Sie ist weder Schlüsselrückgewinnung noch 2PC;
Zeit-/Versuchslimits beweisen keine Erschöpfung außerhalb des deklarierten Raums.

## D. Revidierte Beweisverpflichtungen

1. **Korrektheit:** Differentialtests gegen den Produktionshelper, HMAC-Padding,
   Byte-/Bitreihenfolge, Schlüssel über 64 Bytes und alle JSON-Randfälle; Circuit
   bindet A's Zentrum und erlaubte versteckte Eingabeform oder kontrollierten Adapter.
2. **Autorisierte Records:** Nachweis beider Record-/UUID-Bindungen einschließlich
   Datensatzversion, Schema, Issuer und Epoch; bösartige Teilnehmer dürfen keinen
   beliebigen Zieltoken oder erfundene Kandidaten als authentifiziert ausgeben.
3. **Privatsphäre:** Explizite ideale Funktion, Korruptionsmodell und Leckagebudget;
   keine Behauptung, bekannte lokal verknüpfte Personen blieben gegenüber B anonym.
4. **Beweis zur Implementierung:** Pinning von Compiler, Circuit, Runtime und
   Abhängigkeiten; Prüfung der OT-, MAC-, Sacrifice- und Ausgabeimplementierung
   einschließlich gewählter Parameter und Abbruchverhalten. Funktionstests sind kein Sicherheitsbeweis.
5. **Ergebnisintegrität:** Vertrauenswürdiger Abschlussmarker, keine Ausgabe vor
   Checks; doppelte Signaturen, Audience/Zweck/Ablauf/Session-Bindung, atomarer Replay-Schutz.
6. **Historische Schlüssel:** Authentifizierter Epoch-Fingerprint, tatsächliche
   Retiring-Policy, Legacy-Ausschluss, Widerruf und sichere Cache-/Backup-Löschung;
   keine Unbedenklichkeitsbehauptung nach Schlüsselkompromittierung.
7. **Abfragen und Fehler:** Batchvollständigkeit, Ambiguität, keine partiellen
   Resultate bei Abbruch, gemeinsame Budgets auch über neue Request-IDs und überlappende Sätze.
8. **Klinische Identität:** Tupelgleichheit getrennt von verifiziertem Menschenbezug;
   bei Bedarf zusätzliche issuerqualifizierte Verifikation und klinische Prüfung.
9. **Komposition:** mTLS-/Exportgrenzen, lokale verschlüsselte Speicherung und
   Phase-2-Envelope für externe Blobs bleiben wirksam. Artefakthashes identifizieren
   Dateien. Gemeinsame Inhaltsdigests, Metadaten und exportierte klinische Inhalte
   können unabhängig vom Matching identifizieren/verknüpfen; 2PC beweist keine
   Anonymität des gesamten freigegebenen Datensatzes.

## E. Kleinster gerechtfertigter nächster Schritt

Ein weiterer isolierter Integrationsversuch mit zwei tatsächlich getrennt
administrierten Testendpunkten, synthetischen Daten und einem expliziten
Record-Bindungsvertrag ist gerechtfertigt. Voraussetzungen: dokumentierte
App-/Root-/DB-/Backup-Rechte, authentifizierte Epoch- und Record-Snapshots,
überprüfter Circuit samt Formatbindung und minimaler empfängerbezogener Ausgabe,
gepinntes Backend sowie authentifizierter Kanal. Noch keine Patientenzusammenführung.

Akzeptanz: beide UUID-Substitutionen, fremde/unzulässige Ziele, manipulierte
Ergebnisse, abgelaufene/replayed Sessions, unbekannte Epochs, abgeschnittene
Nachrichten, Prozessabbruch und überschrittene kumulative Abfragebudgets werden
als Fehler ohne Beziehungspersistierung abgewiesen. Doppelte passende Records
bleiben mehrdeutig. Signaturen binden dieselbe erfolgreiche Session. Ein
Produktionsentscheid verlangt zusätzlich Sicherheitsreview, klinische
Identitätsentscheidung und Messung im vorgesehenen Netzwerk.

## Ausgeführte Validierung

Die isolierte [Referenz](../scripts/patient_matching_2pc_reference.py) sieht
beide Inputs und beansprucht **keine** Privatsphäre. Die
[Tests](../tests/scripts/test_patient_matching_2pc_reference.py) verwenden nur
synthetische Namen, UUIDs und Schlüssel unter isolierten Testsettings.
39 Tests bestehen: Produktionsgleichheit, A/B-Schlüssel-/Zentrumtrennung,
Whitespace, Case, Unicode/NFC-NFD, JSON-Escaping, Datum/datetime, lange und
UTF-8-Schlüssel, fehlende/falsch gebundene historische Generation, mehrdeutige
Tupel und unvollständige Suche. Ein ausdrücklicher Negativnachweis zeigt:
UUID-Substitution bleibt im Referenzmodell unentdeckt. Signaturen, Replays und
autorisierte Inputs sind dort **nicht implementiert**.

Ausgeführt, in dieser Reihenfolge vor pytest:

```text
.devenv/state/venv/bin/pyright scripts/patient_matching_2pc_reference.py tests/scripts/test_patient_matching_2pc_reference.py
  0 errors, 0 warnings
.devenv/state/venv/bin/pyright
  20 errors, 24 warnings; vorhandene identity_resolve/generate_public_key.py (17)
  und identity_resolve/sha_reverse.py (3), keine Fehler in den neuen Referenzdateien
.devenv/state/venv/bin/pytest tests/scripts/test_patient_matching_2pc_reference.py -q
  39 passed, 1 bestehende Plugin-Rewrite-Warnung, 0.16s
```

Die nachfolgenden Backendmessungen sind getrennt von diesen Referenztests zu lesen.

### Reproduzierbarer synthetischer Zwei-Prozess-Versuch

Der persistierte [Schaltkreis](../scripts/patient_matching_2pc.mpc) ist vom
Produktionscode isoliert. Die externe Laborsteuerung unter `/tmp` besitzt beide
synthetischen Inputs; die beiden MP-SPDZ-Prozesse erhalten getrennte Player-Inputs.
Dies demonstriert echte 2PC, aber keine getrennten Administratoren: beide
Prozesse und Eingabedateien liegen auf demselben Testhost. Keine klinischen
Daten, Produktionsschlüssel, neuen Pakete im Anwendungsvenv oder Deploymentänderungen.

Reproduktionspins:

| Komponente | Version / SHA-256 |
|---|---|
| [Offizielles Releasearchiv](https://github.com/data61/MP-SPDZ/releases/download/v0.4.4/mp-spdz-0.4.4.tar.xz) | v0.4.4; `73d6473526101b4433999fc4f7521fdfdc53ddf018e5f64480518d6293f62760` |
| Bristol-Schaltkreisrepository | `mkskeller/bristol-fashion`, Commit `1603ed6aa12348c2ef15ed8395b9b96e4c558b7f` |
| `sha256.txt` | `bd0a91bb7e97bb60c1468fe8caecc546af3f832bd4152d9c8c4e7527412dd11d` |
| `bin/Linux-adx/tinier-party.x` | `d6c168f5c35c9c9c5fda31d03e930bcf2f883601824f5128f0ab4d85343f06c8` |

Der anfängliche Quellbuild scheiterte an fehlendem `sodium.h`; das offizielle
Linux-Binary war ausführbar. `tiny-party.x` brach beim OT-Setup an der Assertion
`keyBits == ot_setup.base_receiver_inputs` ab. Die Prüfung wurde nicht entfernt;
das ebenfalls unterstützte **Tinier**-Backend wurde verwendet.

Fixture: ASCII-Key `synthetic-key-not-for-clinical-use` (34 Bytes), JSON
`["patient-identity-v1","Änne","Beispiel","1980-01-02","synthetic-center-a"]`
(76 UTF-8-Bytes). Ziel-HMAC:
`41697558cc6b66f36faab6bf713575f3d59041e04b58b54859e1ff7c3156bfe5`.
Öffentliche Epoch:
`e143ca424402580248ebc5d10a68d5f469bde4de5ddd8d04febad3b6a6a55250`.
Der Key ist ein öffentliches Testfixture, kein zulässiges Produktionsgeheimnis.

Im externen Releaseverzeichnis wird die `.mpc` nach `Programs/Source` und die
gepinnten Bristol-Circuits in das erwartete `Programs/Circuits` eingebunden.
`Player-Data/Input-P0-0` enthält die dezimalen Bytewerte des Keys, danach die
32 dekodierten Bytes des **Ziel-Digests**, jeweils leerzeichengetrennt.
`Input-P1-0` enthält die dezimalen UTF-8-Bytes der JSON-Nachricht, `COUNT`-mal.
Nur der Digest wird aus Hex dekodiert, niemals der konfigurierte Schlüssel.
Diese synthetischen Dateien werden ausschließlich außerhalb der Anwendung
vorbereitet. Reproduktion benötigt eine isolierte Laborumgebung.

```text
python3 compile.py -M -B 64 patient_matching_2pc 34 76 COUNT EPOCH
bin/Linux-adx/tinier-party.x 0 PROGRAM -N 2 -pn 19434 -h 127.0.0.1 -OF .
bin/Linux-adx/tinier-party.x 1 PROGRAM -N 2 -pn 19434 -h 127.0.0.1 -OF .
```

Hier ist `EPOCH` der obige Wert und
`PROGRAM=patient_matching_2pc-34-76-COUNT-EPOCH`, mit ersetzten Platzhaltern.
Die letzten beiden Prozesse laufen gleichzeitig unter einem Supervisor mit
240-Sekunden-Limit, 2-GiB-Adressraumlimit je Prozess und deaktivierten Core-Dumps.
Portbasis und Folgeport müssen frei sein. `-M` erhält Speicherreihenfolge beim
Kompilieren; die zunächst sichtbare Compilerwarnung wird damit vermieden.
Inputs sind byteweise, der Schaltkreis konvertiert explizit zur Bitreihenfolge
des SHA-256-Bausteins. Öffentliche Grenzen: 1–1024 Keybytes, 1–4096 Payloadbytes,
1–100 Kandidaten; Größe/Anzahl und Epoch sind nicht verborgen.

Kein zusätzliches Datenschutzpadding: alle Kandidaten einer Instanz haben
dieselbe öffentliche Bytelänge; SHA-256 verwendet sein normales kryptografisches
Padding. B serialisiert außerhalb der Schaltung. A's Key und Ziel bleiben private
Schaltkreisinputs. Die Epoch wird **innerhalb** der Schaltung mit derselben
HMAC-Funktion geprüft. Es gibt keine exportierten oder gecachten HMAC-Zustände.

Beide Parteien erhalten `epoch_valid` und anschließend pro Kandidat ein gegatetes
Matchbit. `epoch_valid=0` darf nicht als Nonmatch interpretiert werden. Dies ist
kein fertiger Workflow-Resulttyp. Die Ausgabe ist inkrementell: ein späterer
Abbruch kann bereits ausgegebene Teilresultate nicht zurücknehmen. Die Referenz
verhindert dagegen fachliche Teilresultate bei bekannter Unvollständigkeit.
Es gibt keine UUIDs, Signaturen, Replay-Datenbank, Berechtigungsprüfung oder
JSON-Formatvalidierung im Schaltkreis. Ein manipuliertes Ausgabeprotokoll ist
nicht authentifiziert. Malformed-Input-/Transport-Fuzzing und eine vollständige
Interruptions-Testmatrix wurden nicht ausgeführt.

Die Testverbindung verwendet localhost als Peeradresse, **kein mTLS**; ein
weiteres Listen-Interface ist dadurch nicht ausgeschlossen. Das ist kein
Transportprofil für ein feindliches Kliniknetz. Produktionsisolation, separate
Dateirechte, sichere Eingabeadapter, Logging-, Swap-, Crashdump- und Backupregeln
bleiben offen. Der Laborharness hält synthetische Inputdateien und Ausgaben;
dieses Muster darf nicht für reale Geheimnisse übernommen werden.

### Messungen und tatsächlich ausgeführte Backendprüfungen

Intel Core i5-12450H, 12 logische CPUs, 16 GiB RAM, Linux 6.12.112 x86_64,
Python 3.12.13;
MP-SPDZ v0.4.4 Linux-adx-Binary. Alle Messungen sind einzelne lokale Läufe,
keine Mittelwerte oder Hochrechnungen. Die Batchkandidaten wiederholen dieselbe
synthetische 76-Byte-Nachricht, alle Bits sind 1. Sie messen keine heterogene
klinische Suche oder UUID-Zuordnung.

| Kandidaten | Compile/Setup (s) | Laufzeit Wandzeit (s) | Runtime inkl. Preprocessing (s) | Wandzeit/Kandidat (s) | globale Backend-Kommunikation (MB) |
|---:|---:|---:|---:|---:|---:|
| 1 | 20.589 | 3.633 | 3.53942 | 3.633 | 581.641 |
| 10 | 9.941 | 21.392 | 21.295 | 2.1392 | 3 480.96 |
| 100 | 20.41297 | 171.81484 | 171.736 | 1.71815 | 32 482.8 |

„Compile/Setup“ ist nur die Circuit-Kompilation, nicht kryptografische
Vorverarbeitung. Onlinezeit und kryptografisches Setup wurden **nicht separat
instrumentiert**: die Runtimezahl umfasst beides. Compile plus gemessene Wandzeit
betragen 24.222 s für 1, 31.333 s für 10 und 192.22781 s für 100 Kandidaten; einmaliger Download,
Installation und Inputvorbereitung sind nicht enthalten. MB sind die vom Backend
gemeldeten globalen Sendevolumina, keine Paketmitschnittmessung einschließlich
aller Netzwerk-/TLS-Header. Kein extrapolierter 1 000er-Wert; wegen der bereits
hohen Kommunikationskosten wurde 1 000 nicht versucht.

Der 10er-Lauf entstand vor dem Wechsel auf `-M` und zeigte die generische
Speicherreihenfolgewarnung. Die endgültigen 1er-/100er-Läufe verwenden `-M`.
Die Messwerte sind deshalb keine kontrollierte Skalierungsreihe; alle drei
Läufe lieferten funktional die erwarteten Bits. Für belastbare Vergleiche
braucht es Wiederholungen mit identischen Compiler-/Laufparametern.

Die synthetischen Rohmessungen liegen temporär in
`/tmp/lx-matching-backend-1-preserved.json`,
`/tmp/lx-matching-backend-10.json`,
`/tmp/lx-matching-backend-100-preserved.json`,
`/tmp/lx-matching-backend-negative.json` und
`/tmp/lx-matching-backend-long-key.json`; sie sind keine dauerhaften
Produktionsbelege. Fixture, Pins und Kommandos oben ermöglichen Wiederholung.

Peak-RSS wurde nicht gemessen. Das Adressraumlimit von 2 GiB ist keine
Speichermessung; eine punktuelle RSS-Beobachtung von ungefähr 47 MiB je Prozess
beim 100er-Lauf ist ausdrücklich kein Peak. 100 Kandidaten erforderten laut
Backend etwa 1 984 413 Kommunikationsrunden je Partei. LAN-/WAN-Latenz und
gleichzeitige Transfers können das Ergebnis wesentlich verändern.

Tatsächlich erfolgreich: Unicode-JSON produziert den erwarteten HMAC;
falscher Zielwert ergibt `epoch_valid=1, match=0`; falscher Key bei gebundener
Epoch ergibt `0,0`; B's Zentrum statt A's ergibt `1,0`. Ein zusätzlicher
HMAC-Langschlüsseltest (RFC-4231-Testvektor 6: 131 Bytes `0xaa`, Nachricht 54 Bytes)
ergab `1,1`, Wandzeit 3.74453 s, 710.253 MB global. Diese Prüfungen ersetzen keine
adversariale Protokollprüfung. Ein Portkonflikt im Labor verursachte einen
Timeout; nach Wahl getrennter Ports liefen die Fälle. Der Timeout wurde nicht
als Nonmatch gewertet. Ein gezielt unterbrochener vollständiger Workflow mit
Signaturen und atomarer Beziehungspersistierung existiert noch nicht.

Abschließende Strukturprüfung: 65 Manifeste erfolgreich; Feature-Validierung:
37 Definitionen gültig. `tracker.py check hub_transfer` meldet erwartungsgemäß
**NICHT PRODUKTIONSREIF (5/12)**. Der Whole-Project-Pyright-Fehlerbestand bleibt
offen; die isolierten Referenzdateien sind typgeprüft. `.mpc` wird durch den
MP-SPDZ-Compiler geprüft, nicht durch Pyright. Ruff für die Referenzdateien und
der auf die geänderten Dateien begrenzte Whitespace-Diffcheck bestanden.
