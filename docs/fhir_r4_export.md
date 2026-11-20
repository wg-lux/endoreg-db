# FHIR R4 Export: Integration Contract and Operations

## Approved Export Profile

`endoreg_db` provides examinations as a read-only FHIR R4 `Bundle` of type
`collection`. Only the
`pseudonymized` profile is approved. The endpoint is:

```text
GET /endoreg-api/patient-examinations/{id}/fhir/
Content-Type: application/fhir+json
```

In production, access requires authentication and the reader role required by
`PolicyPermission`. Non-privileged users may export only examinations from
their assigned center. A foreign or unresolvable center scope is treated as a
nonexistent resource.

The export contains no names, dates of birth, external hospital IDs, database
primary keys, or free-form report text. A persisted `patient_hash` is required.
If this pseudonym is missing, the entire export fails; there is no identifying
fallback.

## Resources and Cardinalities

| Resource | Count | Source and mapping |
| --- | ---: | --- |
| `Patient` | exactly 1 | Hashed `patient_hash`; no direct demographic identifiers |
| `Procedure` | exactly 1 | Examination type, status, and available examination period |
| `Observation` | 0..n | Active findings and active classifications |
| `ImagingStudy` | 0..n | Fully imported DICOM studies, series, and instance counts |
| `DiagnosticReport` | 0..n | Active reports, status, and references; no free-form final report |

The bundle validates every internal `subject`, `partOf`, `result`, and
`imagingStudy` reference against the entries actually included. Duplicate
`fullUrl` values, duplicate resource identities, and unresolvable references
are rejected.

## Terminologies

The canonical systems currently used are:

- `https://wg-lux.de/fhir/CodeSystem/lx-examination-cs`
- `https://wg-lux.de/fhir/CodeSystem/lx-finding-cs`
- `https://wg-lux.de/fhir/CodeSystem/lx-classification-cs`
- `https://wg-lux.de/fhir/CodeSystem/lx-classification-choice-cs`
- `http://dicom.nema.org/resources/ontology/DCM` for modalities
- `urn:dicom:uid` for DICOM Study Instance UIDs

Local names are normalized deterministically into FHIR codes. An empty or
non-normalizable terminology name causes the export to fail. External
terminology-server validation and national profile conformance are not yet
part of this integration contract.

## Identity, Provenance, and Versioning

The bundle and resources receive stable, opaque FHIR IDs derived from SHA-256
over the resource type and stable source identity. Internal database IDs do not
appear in the wire format. The bundle also contains:

- `meta.profile` with
  `https://wg-lux.de/fhir/StructureDefinition/lx-pseudonymized-endoscopy-bundle`
- `meta.tag` with export contract version `1.0`
- `identifier` as a SHA-256-based reference to the source examination

Two exports of the same database state are semantically and byte-for-byte
stable after canonical JSON sorting. After a domain change to the source, the
resource identity remains stable while its content changes accordingly.

## Missing Data

- A missing examination definition produces a `Procedure` with neutral text
  and status `unknown`.
- Missing start and end dates omit `performedPeriod`.
- No `Observation` is produced without active findings.
- No `ImagingStudy` is produced without fully imported DICOM data.
- No `DiagnosticReport` is produced without active reports.
- A missing patient pseudonym, invalid time period, invalid terminology, or
  inconsistent reference aborts the entire export.

## Example Structure

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

The shortened example does not show the mandatory `Procedure`, which is always
present in the real bundle.

## Observability and Recovery

The `endoreg_db.interoperability.fhir` logger emits structured events:

| Event | Meaning |
| --- | --- |
| `fhir.export_completed` | Bundle fully built and validated |
| `fhir.export_rejected` | Source, resource, or bundle contract invalid |

Events contain only a hashed examination reference, the export profile, the
fixed reason code, and, for errors, the exception type. Direct patient
identifiers and clinical free text are not logged.

For `bundle_build_failed`, correct the source state. Because the service
returns a bundle only after complete validation, no partial bundle can be
released. After correction, an identical GET request is sufficient; no
server-side export state needs to be reset.

Recommended monitoring:

- Alert on `fhir.export_rejected`, grouped by `error_type`
- `completed`-to-`rejected` ratio per deployment
- Regular retrieval of a pseudonymized test case with schema and reference
  validation

## Deliberate Boundaries

FHIR Write, transaction bundles, Search, Subscriptions, Bulk Data, free-form
patient demographics, free-form report text, external terminology-server
validation, and a conformance claim against national Implementation Guides are
not supported. New fields or profiles require a new contract version, a data
protection review, and tracker assessment.
