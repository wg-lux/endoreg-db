# LXDM-Contract-Nutzungsprüfung

Der Status dieser Prüfung wird ausschließlich im Kriterium
`contract_usage_audit` in `feature-tracking/LXDM.yml` geführt. Dieses Dokument
ist eine technische Prüfspezifikation und enthält keinen eigenen
Abschlussstatus.

## Ziel

Die öffentlichen Typing-Contracts unter
`/home/admin/lx-data-models/lx_dtypes/models/contracts` werden vollständig
inventarisiert und mit ihrer tatsächlichen oder möglichen Nutzung in
`endoreg-db` verknüpft. Daraus entstehen kleine, priorisierte
Migrationskohorten, die die LXDM-Nutzung verbessern, ohne Ownership-Grenzen,
persistierte Formate oder klinische und sicherheitsrelevante Invarianten
unbeabsichtigt zu verändern.

## Prüfumfang

Einbezogen werden:

- alle Nicht-Test-Pythonmodule im Contract-Verzeichnis sowie öffentliche
  Re-Exports aus `__init__.py`;
- die zugehörigen Contract-Tests als Nachweis bestehender Invarianten;
- statische Imports, Re-Exports und dynamische Consumer in Quellcode, Tests und
  Konfiguration von `endoreg-db`;
- relevante API-, Persistenz-, Job-, Import-/Export-, Authentifizierungs- und
  Mediengrenzen;
- Abhängigkeits- und Versionsgrenzen in den Paket- und Lockdateien.

Die Prüfung implementiert noch keine Contract- oder Consumer-Änderungen.
Generierte Dateien und Migrationen dürfen als Nutzungsnachweis dienen, sind
aber nicht automatisch Änderungsziele.

## Leitfragen

Für jeden Contract wird geprüft:

1. Welche öffentlichen Typen, validierten Felder und Invarianten stellt das
   Modul bereit?
2. Wo wird der Contract in `endoreg-db` importiert, re-exportiert, indirekt
   aufgelöst oder inhaltlich nachgebildet?
3. Existieren parallele Pydantic-Modelle, Dataclasses, TypedDicts oder
   untypisierte Dictionaries mit derselben fachlichen Bedeutung?
4. Liegt die Ownership des Schemas bei LXDM, bei `endoreg-db` oder an einer
   expliziten Adaptergrenze?
5. Welche Lücken bestehen bei Boundary-Validierung, Versionierung,
   Rückwärtskompatibilität und automatisierten Tests?

## Vorgehen

1. Contract-Module, öffentliche Typen, Re-Exports und bestehende Tests werden
   maschinenunterstützt inventarisiert.
2. Consumer und semantisch parallele lokale Typen in `endoreg-db` werden mit
   statischer Suche und gezielter Callsite-Prüfung ermittelt.
3. Jede Nutzung wird einer konkreten Systemgrenze und einem Owner zugeordnet.
4. Jeder Fund wird als `direct_use`, `adapter_required`,
   `candidate_for_adoption`, `endoreg_owned`, `unused_or_uncertain` oder
   `deprecation_candidate` klassifiziert.
5. Verbesserungen werden nach Risiko und Nutzen in kleine Migrationskohorten
   gegliedert. Jede Kohorte benennt Zielcontract, betroffene Dateien,
   Kompatibilitätsanforderungen und Verifikation.
6. Vor einer späteren Umsetzung werden die bestehenden LXDM-Contract-Tests,
   Pyright und die jeweils engsten Endoreg-Tests als Ausgangsbasis festgehalten.

## Erforderliche Ergebnisfelder

Das Prüfergebnis muss pro öffentlichem Contract mindestens folgende Felder
enthalten:

| Feld | Inhalt |
| --- | --- |
| `contract_module` | Vollständiger Modulpfad |
| `public_type` | Öffentlicher Typ oder Re-Export |
| `contract_owner` | Zuständiges Repository oder Team |
| `endoreg_consumer` | Konkrete Consumer-Datei oder begründet `keiner` |
| `boundary` | Betroffene System- oder Persistenzgrenze |
| `current_shape` | Aktuelle Repräsentation in `endoreg-db` |
| `classification` | Eine der definierten Nutzungsklassen |
| `gap_or_risk` | Typing-, Validierungs-, Kompatibilitäts- oder Ownership-Risiko |
| `recommended_action` | Begrenzte, umsetzbare Verbesserung |
| `verification` | Pyright-, Test- oder Review-Nachweis |

## Architektur- und Sicherheitsleitplanken

- Contract-Daten werden an externen und persistierten Grenzen einmal validiert
  und intern in einer eindeutigen typisierten Form weitergegeben.
- `Any`, offene Dictionaries oder optionale Felder dürfen nicht allein zur
  Kompatibilisierung ausgeweitet werden.
- Contract-Änderungen in LXDM und deren Adoption in `endoreg-db` werden als
  getrennte Änderungen mit eigener Ownership bewertet.
- Öffentliche und persistierte Verträge benötigen eine explizite Versions- und
  Rückwärtskompatibilitätsstrategie.
- Bei Video-Contracts gelten zusätzlich die kanonischen Regeln für
  Präsentationszeitstempel, Speicherprofile, Verschlüsselungsgrenzen und
  fehlersichere Bereinigung.

## Erwartete Liefergegenstände

- vollständiges Contract-zu-Consumer-Inventar mit begründeten Nullfunden;
- Liste paralleler lokaler Schemata und untypisierter Boundary-Payloads;
- priorisierte Migrationskohorten mit Owner, Risiko und betroffenen Dateien;
- reproduzierbare Pyright-, Test- und Review-Nachweise je Kohorte.

Fortschritt und Verifikation dieser Liefergegenstände werden ausschließlich
über `feature-tracking/LXDM.yml` aktualisiert.

## Vollständiges Inventar und Ergebnis

Das reproduzierbare Ergebnis liegt in
[`lxdm_contract_inventory.md`](lxdm_contract_inventory.md). Es wird durch
`feature-tracking/audit_lxdm_contracts.py` direkt aus beiden Repositories
erzeugt und erfasst alle 111 öffentlichen Nicht-Test-Contract-Module samt
Exporten/Re-Exports, statisch erkennbaren Invarianten, LXDM-Tests,
Endoreg-Consumern und Boundary-Klassifikation.

Der aktuelle Stand ordnet 79 Module als `direct_use`, vier als
`candidate_for_adoption`, vier als `adapter_required` und 24 als
`unused_or_uncertain` ein. `unused_or_uncertain` ist ausdrücklich kein
Löschsignal; diese Module bleiben LXDM-owned, bis ein fachlicher Owner ihren
Einsatz oder eine Deprecation bestätigt.

## Priorisierte Migrationskohorten

1. **P0 – persistierter klinischer LXDM-Datensatz.** Zielcontract:
   `dtypes_record_persistence`. Consumer: `schemas/persisted_json.py`,
   `services/dtypes_records.py`, PatientExamination-Modell/API. Risiko: der
   0.2.0-Contract war offen und unvollständig. Maßnahme: vollständiger strikt
   verschachtelter 0.2.1-Contract, ein Parser am Rand, Host-ID-Prüfung und
   Roundtrip/Unknown-field-Tests. Owner: lx-data-models für die Form,
   endoreg-db für ORM, Autorisierung und Persistenz.
2. **P0 – Hub-Segmentaustausch.** Zielcontract: `hub_transfer`. Consumer:
   TransferJob-Schema/Serializer und Sender-Payload. Risiko: parallele lokale
   Segmentmodelle können auseinanderlaufen. Maßnahme: Endoreg-Kompatibilitätsname
   erbt vom kanonischen LXDM-Segmentvertrag; vollständige Transfer-DTO-Adoption
   folgt nach 0.2.1-Pin. Verifikation: Contract-, Transferjob- und Hub-Endpunkttests.
3. **P1 – Authentifizierte Finding-Mutationen.** Zielcontracts: `authz`,
   `permission_runtime`, `patient_finding*`; Host-Callback-Grenze bleibt
   Endoreg-owned. Maßnahme: Authentifizierung, Center-404, Akteur/Zeit und
   atomaren Record-Refresh gemeinsam regressionsprüfen.
4. **P2 – Medien-, Anonymisierungs- und Exportcontracts.** Die direkt genutzten
   Module werden boundaryweise beibehalten; lokale Dicts werden nur in kleinen
   Kohorten mit Pyright und dem jeweils engsten Medien-/Exporttest ersetzt.
5. **P3 – ungenutzte oder dynamische Contracts.** Keine spekulative Adoption.
   Pro Domäne Owner bestimmen, semantische Duplikate prüfen und anschließend
   `direct_use`, `endoreg_owned` oder `deprecation_candidate` begründet setzen.

Contract-Änderungen werden zuerst in lx-data-models mit Contract-Tests und
SemVer dokumentiert. Die Endoreg-Adoption bleibt ein separater Adapter-Schritt
und wird gegen den Kandidaten geprüft, bevor die veröffentlichte Version im
Lockfile angehoben wird.

## Bearbeitete Kohorte: Application Settings

| Feld | Ergebnis |
| --- | --- |
| `contract_module` | `lx_dtypes.models.contracts.application_settings` |
| `public_type` | Backup-Source, Backup-Status, Datensatz-Eintrag, Deployment-Profil und Gesamt-Payload |
| `contract_owner` | LXDM für transportierbare Payloads; `endoreg-db` für Django-Produktion und API-Ausgabe |
| `endoreg_consumer` | `endoreg_db.views.misc.application_settings` und `endoreg_db.services.hub.deployment` |
| `boundary` | Application-Settings-API und Backup-Vorprüfung |
| `current_shape` | Pydantic-Payloads; das Deployment-Profil war zuvor ein offenes Dictionary |
| `classification` | `direct_use` mit notwendiger Contract-Erweiterung |
| `gap_or_risk` | Lose Deployment-Daten erlaubten unbekannte Felder und veröffentlichten interne mTLS-Metadaten; Backup-Zähler hatten keine Konsistenzprüfung |
| `recommended_action` | Deployment-Profil strikt typisieren, abgeleitete Flags validieren, interne mTLS-Metadaten nicht veröffentlichen und Backup-Zähler gegeneinander prüfen |
| `verification` | LXDM-Contract-Tests, Endoreg-Pyright sowie fokussierte Service- und API-Tests |

Das Contract-Modul wird von `endoreg-db` benötigt und bleibt bestehen. Die
Kohorte erweitert und nutzt es direkt; gelöscht werden ausschließlich die für
den öffentlichen Settings-Payload nicht erforderlichen mTLS-Metadatenfelder.

Nachweise der Kohorte:

- LXDM-Pyright für Contract und Contract-Test: 0 Fehler;
- LXDM-Contract-Tests: 10 bestanden;
- vollständiger Endoreg-Pyright-Lauf mit lokalem LXDM-Checkout: 0 Fehler;
- fokussierte Endoreg-Service- und API-Tests: 42 bestanden.

Vor einer getrennten Auslieferung von `endoreg-db` muss die erweiterte
LXDM-Version veröffentlicht und die derzeitige Abhängigkeit
`lx-dtypes==0.2.0` kontrolliert angehoben werden. Der lokale Geschwisterpfad in
`pyrightconfig.json` dient ausschließlich der gemeinsamen Entwicklung und
ersetzt keine Paketversionierung.

## Bearbeitete Kohorte: Authentifizierung und Autorisierung

| Feld | Ergebnis |
| --- | --- |
| `contract_module` | `lx_dtypes.models.contracts.authz` |
| `public_type` | Keycloak-Claims, Rollencontainer, Token-Response, Route-Lookup und Validatoren |
| `contract_owner` | LXDM für die normalisierten Identity-Provider-Payloads; `endoreg-db` für Policy und Gruppensynchronisation |
| `endoreg_consumer` | `endoreg_db.authz.auth`, `endoreg_db.authz.backends`, `endoreg_db.authz.policy`, `endoreg_db.authz.views_auth` und `endoreg_db.views.auth.keycloak` |
| `boundary` | OIDC-Login, Bearer-JWT-Authentifizierung, Token-Austausch und Route-Autorisierung |
| `current_shape` | Direkte Pydantic-Validierung; Bearer-JWT und Browser-OIDC verwendeten unterschiedliche Client-Rollenregeln |
| `classification` | `direct_use` mit notwendiger Contract-Erweiterung |
| `gap_or_risk` | `role_names` übernahm Rollen aus allen Keycloak-Ressourcen und konnte gleichnamige Rollen eines fremden Clients autorisieren |
| `recommended_action` | Flat- und Realm-Rollen als sicheren Default normalisieren; Client-Rollen nur über eine explizit ausgewählte Ressource zugänglich machen; beide Endoreg-Loginpfade vereinheitlichen |
| `verification` | LXDM-Contract-Tests, Endoreg-Pyright sowie fokussierte JWT- und Policy-Tests |

Das Contract-Modul wird vollständig benötigt und bleibt bestehen. Endoreg nutzt
den sicheren `role_names`-Default direkt. `resource_access` bleibt im Contract
erhalten, kann aber nur über `role_names_for_resource` für genau einen explizit
benannten Client in die Autorisierung einbezogen werden.

Nachweise der Kohorte:

- LXDM-Pyright für Contract und Contract-Test: 0 Fehler;
- LXDM-Contract-Tests: 6 bestanden;
- vollständiger Endoreg-Pyright-Lauf mit lokalem LXDM-Checkout: 0 Fehler;
- fokussierte JWT-, OIDC- und Policy-Tests: 7 bestanden.
