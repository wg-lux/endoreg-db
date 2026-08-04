# FHIR-R4-Export: Integrationsvertrag und Betrieb

## Freigegebenes Exportprofil

`endoreg_db` stellt Untersuchungen als read-only FHIR-R4-`Bundle` vom Typ
`collection` bereit. Freigegeben ist ausschließlich das Profil
`pseudonymized`. Der Endpunkt lautet:

```text
GET /api/patient-examinations/{id}/fhir/
Content-Type: application/fhir+json
```

In Produktion erfordert der Zugriff Authentisierung und die durch
`PolicyPermission` verlangte Leserolle. Nicht privilegierte Nutzer dürfen nur
Untersuchungen ihres zugeordneten Centers exportieren. Ein fremder oder nicht
auflösbarer Center-Scope wird wie eine nicht vorhandene Ressource behandelt.

Der Export enthält keine Namen, Geburtsdaten, externen Krankenhaus-IDs,
Datenbank-Primärschlüssel oder freien Berichtstext. Voraussetzung ist ein
persistierter `patient_hash`. Fehlt dieses Pseudonym, schlägt der gesamte
Export fehl; es gibt keinen identifizierenden Fallback.

## Ressourcen und Kardinalitäten

| Ressource | Anzahl | Quelle und Abbildung |
| --- | ---: | --- |
| `Patient` | genau 1 | gehashter `patient_hash`; keine direkten demografischen Identifikatoren |
| `Procedure` | genau 1 | Untersuchungsart, Status und vorhandener Untersuchungszeitraum |
| `Observation` | 0..n | aktive Befunde und aktive Klassifikationen |
| `ImagingStudy` | 0..n | vollständig importierte DICOM Studies, Series und Instanzanzahlen |
| `DiagnosticReport` | 0..n | aktive Berichte, Status und Referenzen; kein freier Schlussbericht |

Das Bundle validiert jede interne `subject`-, `partOf`-, `result`- und
`imagingStudy`-Referenz gegen die tatsächlich enthaltenen Einträge. Doppelte
`fullUrl`-Werte, doppelte Ressourcenidentitäten und nicht auflösbare
Referenzen werden abgewiesen.

## Terminologien

Die derzeit verwendeten kanonischen Systeme sind:

- `https://wg-lux.de/fhir/CodeSystem/lx-examination-cs`
- `https://wg-lux.de/fhir/CodeSystem/lx-finding-cs`
- `https://wg-lux.de/fhir/CodeSystem/lx-classification-cs`
- `https://wg-lux.de/fhir/CodeSystem/lx-classification-choice-cs`
- `http://dicom.nema.org/resources/ontology/DCM` für Modalitäten
- `urn:dicom:uid` für DICOM Study Instance UIDs

Lokale Namen werden deterministisch in FHIR-Codes normalisiert. Eine leere
oder nicht normalisierbare Terminologiebezeichnung lässt den Export fehlschlagen.
Eine externe Terminologieserver-Validierung oder nationale Profilkonformität
ist noch nicht Bestandteil dieses Integrationsvertrags.

## Identität, Provenienz und Versionierung

Bundle und Ressourcen erhalten stabile, opake FHIR-IDs aus SHA-256 über
Ressourcentyp und stabile Quellidentität. Interne Datenbank-IDs erscheinen
nicht im Wire-Format. Das Bundle enthält zusätzlich:

- `meta.profile` mit
  `https://wg-lux.de/fhir/StructureDefinition/lx-pseudonymized-endoscopy-bundle`
- `meta.tag` mit Exportvertragsversion `1.0`
- `identifier` als SHA-256-basierte Referenz auf die Quelluntersuchung

Zwei Exporte desselben Datenbankzustands sind semantisch und byteweise nach
kanonischer JSON-Sortierung stabil. Nach einer fachlichen Änderung an der
Quelle bleibt die Ressourcenidentität stabil, während sich der Inhalt
entsprechend ändert.

## Fehlende Daten

- Eine fehlende Untersuchungsdefinition erzeugt eine `Procedure` mit
  neutralem Text und Status `unknown`.
- Fehlende Start- und Enddaten lassen `performedPeriod` weg.
- Ohne aktive Befunde gibt es keine `Observation`.
- Ohne vollständig importierte DICOM-Daten gibt es keine `ImagingStudy`.
- Ohne aktive Berichte gibt es keinen `DiagnosticReport`.
- Ein fehlendes Patientenpseudonym, eine ungültige Zeitspanne, ungültige
  Terminologie oder eine inkonsistente Referenz bricht den gesamten Export ab.

## Beispielstruktur

```json
{
  "resourceType": "Bundle",
  "id": "bundle-<opaque-id>",
  "meta": {
    "profile": [
      "https://wg-lux.de/fhir/StructureDefinition/lx-pseudonymized-endoscopy-bundle"
    ],
    "tag": [
      {
        "system": "https://wg-lux.de/fhir/CodeSystem/lx-export-version",
        "code": "1.0"
      }
    ]
  },
  "identifier": {
    "system": "https://wg-lux.de/fhir/sid/endoreg-db/examination-pseudonym-sha256",
    "value": "<sha256>"
  },
  "type": "collection",
  "entry": [
    {
      "fullUrl": "Patient/patient-<opaque-id>",
      "resource": {
        "resourceType": "Patient",
        "id": "patient-<opaque-id>",
        "identifier": [
          {
            "system": "https://wg-lux.de/fhir/sid/endoreg-db/patient-pseudonym-sha256",
            "value": "<sha256>"
          }
        ]
      }
    }
  ]
}
```

Das gekürzte Beispiel zeigt nicht die verpflichtende `Procedure`, die im
realen Bundle immer vorhanden ist.

## Beobachtbarkeit und Wiederanlauf

Der Logger `endoreg_db.interoperability.fhir` erzeugt strukturierte Ereignisse:

| Ereignis | Bedeutung |
| --- | --- |
| `fhir.export_completed` | Bundle vollständig aufgebaut und validiert |
| `fhir.export_rejected` | Quelle, Ressource oder Bundlevertrag ungültig |

Ereignisse enthalten nur einen gehashten Untersuchungsbezug, das Exportprofil,
den festen Reason-Code und bei Fehlern den Exception-Typ. Direkte
Patientenidentifikatoren oder klinische Freitexte werden nicht protokolliert.

Bei `bundle_build_failed` muss die Quellkonstellation korrigiert werden. Da der
Service erst nach vollständiger Validierung ein Bundle zurückgibt, kann kein
partielles Bundle freigegeben werden. Nach Korrektur ist ein identischer
GET-Aufruf ausreichend; es existiert kein serverseitiger Exportzustand, der
zurückgesetzt werden müsste.

Empfohlenes Monitoring:

- Alarmierung auf `fhir.export_rejected`, gruppiert nach `error_type`
- Verhältnis `completed` zu `rejected` pro Deployment
- regelmäßiger Abruf eines pseudonymisierten Testfalls mit Schema- und
  Referenzvalidierung

## Bewusste Grenzen

Nicht unterstützt werden FHIR-Write, Transaktionsbundles, Search, Subscriptions,
Bulk Data, freie Patientendemografie, freie Berichtstexte, externe
Terminologieserver-Validierung und eine Konformitätszusage zu nationalen
Implementation Guides. Neue Felder oder Profile benötigen eine neue
Vertragsversion, Datenschutzprüfung und Trackerbewertung.
