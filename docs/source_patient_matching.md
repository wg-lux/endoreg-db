# Patientenidentität und privater zentrenübergreifender Abgleich

Verbindlicher Umfang: [HubTransfer.yml](../feature-tracking/HubTransfer.yml),
Kriterium `local_identity_hash_boundary`, sowie
[Anonymization.yml](../feature-tracking/Anonymization.yml),
Kriterium `identity_hash_response_boundary`.

Patienten- und Untersuchungshashes sowie Identitätsfingerprints bleiben auf dem
Quellknoten. Sie dürfen auch in verschachtelten Metadaten, Wiederholungen oder
verschlüsselten Transferpaketen nicht an den Hub übertragen werden. Der Sender
weist solche Nutzdaten vor dem Netzwerkaufruf zurück; der Empfänger vor der
Registrierung. Datei-Inhaltsprüfsummen bleiben für die Medienintegrität zulässig.

Transfervertrag 5.0 überträgt persistierte Patienten- und Untersuchungs-UUIDs
(Universally Unique Identifiers); ältere Identitätshash-Verträge werden
abgewiesen. UUIDs erhalten bestehende Verknüpfungen, erkennen jedoch nicht von
selbst dieselbe Person in unabhängig angelegten Datensätzen. Bestehende lokale
Patienten, Hash-Aliase, Identitätsschlüssel und Importabläufe bleiben erhalten.

Der [Benchmark](../scripts/benchmark_sha256.md) zeigt eine Kandidatensuche bei
bekanntem Schlüssel und begrenztem Eingaberaum, keine allgemeine mathematische
Umkehrung von SHA-256. Die Übertragungssperre schützt dennoch vor der Offenlegung
erratbarer Patientenidentität. Transportverschlüsselung ersetzt diese Grenze nicht.

Zentrale Salt-Bereitstellung ist deaktiviert; siehe
[Identitäts-Keyring](identity_ring_enrollment.md). Bereits verteilte Salts oder
bereits gespeicherte Hub-Daten werden durch eine Quellcodeänderung nicht entfernt.
Eine Bestandsbereinigung oder Schlüsselrotation benötigt einen eigenen geprüften
Migrationsablauf und darf lokale Wiedererkennung nicht beschädigen.

## Ansatz A: bestehende HMACs privat vergleichen

Die [gesonderte Evaluation](patient_matching_2pc_assessment.md) untersucht exakt
den bestehenden HMAC über Name, Geburtsdatum und das **Quellzentrum A**, unter
dem bestehenden Schlüssel **von A**. Zentrum B benötigt dafür seine autorisierten
Originalmerkmale; sein eigener HMAC genügt nicht. Eine solche Zwei-Parteien-
Berechnung benötigt keinen neuen gemeinsamen zentralen Identitätsschlüssel.
Sie kann Tupelgleichheit feststellen, beweist aber keine eindeutige reale Person.
Der isolierte Versuch gehört zu `existing_hmac_2pc_evaluation` im Tracker und
ändert weder UUIDs noch lokale Hashes oder die Produktionsfreigabe.

Hub/Vault-Kolokation auf gs-02 schließt einen direkten A/B-Abgleich nicht aus.
Entscheidend ist, ob ein Administrator beide vollständigen privaten Eingaben
lesen kann; die aktuelle LuxNix-Konfiguration sieht gemeinsame administrative
SSH-Zugänge vor, deren effektive Privilegien noch geprüft werden müssen.
Die folgende VOPRF-Skizze ist ein **anderes Verfahren mit anderer Funktion und
Migration**, kein kompatibler Ersatz für den bestehenden HMAC.

## Privater Abgleich verifizierter gemeinsamer Patientenkennungen

Der angeforderte zusätzliche Anwendungsfall ist die Entdeckung gemeinsamer
Patienten über eine **verifizierte gemeinsame Kennung mit ausstellender Instanz**.
Für diesen Produktionsvertrag sind Name und Geburtsdatum keine verifizierten
Kennungen. Die separate Ansatz-A-Evaluation darf sie als Tupel vergleichen.
Umfang und Nachweise des verifizierten Kennungsabgleichs gehören
zum Kriterium `cross_site_private_patient_matching` in
[HubTransfer.yml](../feature-tracking/HubTransfer.yml).

Eine Kennung braucht einen verbindlichen Aussteller-Namensraum, dessen
Normalisierungsregeln und nachvollziehbare Verifikation. Führende Nullen dürfen
nicht pauschal entfernt werden. `PatientExternalID.origin` und `external_id`
speichern derzeit lediglich Herkunft und Wert; sie belegen keine Verifikation.
Ein Quellsystem darf solche Datensätze nicht automatisch als verifiziert ausgeben.

### Grenzen der vorhandenen Infrastruktur

Die Prüfung der eingecheckten LuxNix-Konfiguration am 19. Oktober 2026 zeigt:
`ansible/inventory/host_vars/gs-02.yml:84` aktiviert den zentralen Hub;
derselbe Host aktiviert Vault ab Zeile 146. Die Rollen in
`modules/nixos/luxnix/vault/default.nix:403` erlauben knotenspezifische Zertifikate
und Geheimniszugriffe, stellen aber keinen privaten Abgleich bereit. Laut
`docs/vault-hub-machine-enrollment.md:47` wird keine menschliche Anmeldung oder
Enrollment-Administratorrolle provisioniert. Das belegt keine unabhängig
administrierte Schlüsselinstanz. Diese Feststellung beschreibt Quellkonfiguration,
keinen verifizierten Laufzeitzustand.

Ein zusätzlicher Prozess, Container oder Vault-Pfad auf demselben Host schützt
nicht vor dessen Administratoren oder vollständiger Host-Kompromittierung. Eine
unabhängige Abgleichsinstanz benötigt getrennte Administratorzugänge,
Bereitstellungsrechte, Schlüsselhaltung, Sicherungen und Wiederherstellungsrechte.
Der Hub darf keine Berechtigung erhalten, Identitätskandidaten auszuwerten.

### Protokollanforderungen

Als Baustein kommt eine verifizierbare obliviose Pseudozufallsfunktion
(Verifiable Oblivious Pseudorandom Function, VOPRF) gemäß
[RFC 9497](https://www.rfc-editor.org/rfc/rfc9497.html) infrage. Sie verbirgt die
Eingabe vor dem Schlüsseldienst und erlaubt dem Client, die Auswertung gegen
einen zuvor vertrauenswürdig provisionierten öffentlichen Schlüssel zu prüfen.
Sie ist allein kein vollständiges sicheres Patientenabgleichsprotokoll.

1. Die Quelle verifiziert die Kennung nach der festgelegten Ausstellerpolicy.
   Ein versionsgebundener, eindeutig kodierter Abgleichsinput enthält Zweck,
   Abgleichsraum, Aussteller und normalisierte Kennung. Lokale UUIDs und
   Quellknoten gehören nicht in diesen Input, da sonst kein siteübergreifender
   Gleichheitsvergleich möglich ist.
2. Die Quelle blendet den Input und lässt ihn durch den unabhängig verwalteten
   Dienst auswerten. Sie prüft den kryptografischen Nachweis und die zugelassene
   Schlüsselgeneration. Fehler und unbekannte Schlüssel brechen den Vorgang ab.
3. Eine separat signierte Meldung bindet das Ergebnis an Quellknoten,
   Patienten-UUID, Abgleichsraum, Schlüsselgeneration, Verifikationsbeleg,
   Empfänger, Ablaufzeit und Wiederholungsschutz. Signatur- und
   Schlüsselaustauschschlüssel bleiben getrennt.
4. Der Hub hält bestätigte Beziehungen zwischen unveränderten Quell-UUIDs fest.
   Er überschreibt keine Patientenidentität, führt Datensätze nicht still
   zusammen und verwechselt Dokument-Inhaltsgleichheit nicht mit Patientengleichheit.
   Widersprüche, Widerrufe und Schlüsselmigration benötigen explizite Regeln.

VOPRF-Nachweise bestätigen eine korrekte Schlüsselberechnung, **nicht** die
Echtheit der Patientenkennung. Eine Knotensignatur identifiziert den Absender,
beweist aber nicht die Wahrheit seiner Aussage. Gegen bösartige Quellen braucht
es eine vertrauenswürdige Verifikationsgrenze mit Bindung an die Auswertung oder
ein gesondert geprüftes Credential-Protokoll. Eigene Kurvenarithmetik oder
selbst entworfene Zero-Knowledge-Nachweise sind keine zulässige Abkürzung.

Gleichheitstoken offenbaren dem Hub Gleichheit und Wiederkehr. Sie sind sensible
Pseudonyme und keine anonymen Daten. Auswertungsrechte, Mengenbegrenzungen,
Auditierung ohne Kennungswerte und ein Verbot frei abfragbarer Existenz-Orakel
begrenzen Missbrauch, beseitigen jedoch nicht das Risiko autorisierter
Rateversuche. Wer Schlüssel und Tokenbestand zusammen erhält, kann mögliche
Kennungen offline testen. Diese Grenze muss im Betriebsmodell ausdrücklich
berücksichtigt werden.

Die vorhandene X25519-Transportverschlüsselung bleibt getrennt vom Abgleich.
Passworthashes, Django `SECRET_KEY`, `NetworkNode.shared_secret` und lokale
Master-Schlüssel sind weder Abgleichsschlüssel noch Saatwerte für asymmetrische
Schlüssel. Private Schlüssel werden nicht aus Patientenmerkmalen abgeleitet.
Die zusätzliche Abgleichsschnittstelle darf die bestehenden Medien- und
Browser-Übertragungssperren nicht pauschal lockern.

## Browser APIs and exports

Browser routes use local numeric patient/examination IDs and case UUIDs. Patient,
case, timeline, anonymization metadata and cohort responses omit identity hashes,
including shortened displays. Case creation resolves the supplied patient ID only
within the authenticated user's authorized centers. Cohort grouping continues to
use hashes internally; those grouping keys are never serialized.

FHIR export version 2.0 identifies persisted local patients and examinations
without hashing or publishing their demographic identity hashes. Selected-case
JSON/XLSX contract 2.0 uses `patient_id`; study workbooks contain local patient and
examination IDs. These are source-local identifiers, not cross-node matching keys.
Workbook builders validate their contracts before serialization. The HTTP guard
still rejects identity-hash fields, but no longer rejects every spreadsheet.
Annotation and timestamp CSV streams validate their fixed column headers before
publication. Other deferred metadata streams remain blocked until they have an
explicit validated contract; binary media streaming and explicit
file-integrity checksums remain available. Displayed timeline/case media names use
resource IDs rather than protected source filenames.

Deploy the shared contracts, backend and frontend together. The response guard
cannot roll back a completed local mutation; do not automatically retry writes
when an incompatible old client receives a blocked response.
