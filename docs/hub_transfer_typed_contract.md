# Typisierter Hub-Transfervertrag 3.0

[English version](hub_transfer_typed_contract.en.md)

Dieses Dokument ist die technische Integrations- und Portierungsanleitung für
Sender, die anonymisierte, verarbeitete Medien an einen aktuellen
`endoreg_db`-Central-Hub übertragen. Der verbindliche Fertigstellungsstatus
steht ausschließlich in
[`feature-tracking/HubTransfer.yml`](../feature-tracking/HubTransfer.yml).

Die Anleitung richtet sich insbesondere an Implementierungen, die noch auf dem
älteren Payload-Schema `1.0` oder auf untypisierten `dict[str, Any]`-Payloads
basieren. Solche Implementierungen dürfen nicht durch Aufweichen des Receivers
kompatibel gemacht werden. Sie müssen vor dem ersten Netzwerkzugriff auf den
gemeinsamen Vertrag `3.0` migriert werden.

## Verbindliche Quellen und Schichtengrenzen

| Verantwortung | Verbindliche Quelle | Aufgabe |
| --- | --- | --- |
| Repository-übergreifender Wire-Vertrag | `lx_dtypes.models.contracts.hub_transfer` | Strikte, eingefrorene Pydantic-Modelle, typisierte Rückgabewerte und kanonische Serialisierung |
| Endoreg-Persistenzgrenze | `endoreg_db.schemas.persisted_json` | Validierung und Kanonisierung von `resource_rows` und `processing_snapshot` vor Datenbankspeicherung als JavaScript Object Notation (JSON) |
| Hypertext-Transfer-Protocol-(HTTP)-Grenze | `endoreg_db.serializers.hub.transfer_job.TransferJobCreateSerializer` | Schema-Version, Node-/Center-Ownership, Datenschutz, Anonymisierungszustand und Hashverknüpfungen |
| Persistenz | `endoreg_db.models.hub.transfer_job.TransferJob` | Felder, Choices, Constraints und erneute JSON-Validierung in `clean()`/`save()` |
| Receiver-Workflow | `endoreg_db.services.hub.transfers` | Replay, Medienintegrität, atomare Speicherung, Zustandsübergänge und Acknowledgement |
| Sender-Workflow | `lx_annotate.hub.hub_export_payloads` und `hub_export_worker` | Payloadaufbau, lokale Validierung, mutual Transport Layer Security (mTLS), Retry und Acknowledgement-Prüfung |

Neue gemeinsame Felder werden zuerst in `lx_dtypes` modelliert. Endoreg-spezifische
Persistenzfelder bleiben in `endoreg_db.schemas`. Request-Verarbeitung gehört
nicht in Django-Modelle, und Netzwerk- oder Dateisystemoperationen gehören
nicht in Pydantic-Modelle.

## Unverhandelbare Regeln

- `payload_schema_version` ist exakt `"3.0"`. `"1.0"` und `"2.0"` werden
  abgelehnt.
- Zulässiger Produktionsmodus ist
  `metadata_and_processed_media`. Raw-Medien werden weder registriert noch
  hochgeladen.
- Ein Medium ist nur transferfähig, wenn sein Anonymisierungszustand
  `VALIDATED` ergibt. `ANONYMIZED` oder
  `DONE_PROCESSING_ANONYMIZATION` allein reichen nicht.
- `source_center_key` muss dem `owning_center` des authentifizierten
  `NetworkNode` entsprechen. Eine Django-Benutzersitzung ist für diese
  Machine-to-Machine-Endpunkte weder Quelle noch Ersatz des Center-Scopes.
- Direkte Identitätsfelder wie Name oder Geburtsdatum sind verboten.
  `sensitive_meta` enthält ausschließlich `patient_hash` und
  `examination_hash` als kanonische, kleingeschriebene 64-stellige
  Secure-Hash-Algorithm-256-Bit-(SHA-256)-Hexwerte.
- Reports enthalten nur `anonymized_text`; das Feld `text` ist verboten.
- Der SHA-256-Hash des tatsächlich hochgeladenen Processed-Mediums wird vor
  dem Payloadaufbau neu berechnet. Er steht für Videos in
  `video_file.processed_video_hash` und
  `video_state.processed_file_sha256`, für Reports in
  `raw_pdf_state.processed_file_sha256`.
- Payload, Datei und Remote-Acknowledgement werden als untrusted input
  behandelt, auch wenn der Transport mTLS verwendet.
- `NetworkNode.shared_secret` authentifiziert Requests. Es ist kein
  Verschlüsselungsschlüssel. Master-Key und Raw-Medien verlassen niemals die
  lokale Schutzgrenze.

## Warum zwei Validierungsebenen existieren

Der Sender validiert den vollständigen Wire-Payload mit `lx_dtypes`, bevor
Metadaten oder Medien offengelegt werden. Der Receiver validiert denselben
Vertrag erneut an der HTTP-Grenze und kanonisiert anschließend die persistierten
JSON-Teilobjekte. Das ist keine konkurrierende Fachlogik:

1. `lx_dtypes` verhindert, dass ein inkompatibler Sender einen Request beginnt.
2. Der Serializer schützt den Receiver vor einem veralteten oder manipulierten
   Client und löst lokale Node-/Center-Referenzen auf.
3. `TransferJob.save()` schützt direkte Schreibpfade des
   Object-Relational-Mappers (ORM) und spätere Updates.

Die Rückgabe des Validators ist der kanonische Payload. Der ursprüngliche
unvalidierte Mapping-Wert darf danach nicht weitergereicht werden.

```python
from typing import Any, cast

from lx_dtypes.models.contracts import validate_hub_transfer_video_payload
from lx_dtypes.models.contracts.hub_transfer import (
    HubTransferVideoTransferPayloadData,
)

candidate: dict[str, Any] = build_candidate_payload()
payload: HubTransferVideoTransferPayloadData = (
    validate_hub_transfer_video_payload(candidate)
)

# Ab hier ausschließlich `payload` verwenden, nicht mehr `candidate`.
send_json(cast(dict[str, Any], payload))
```

Ein `ValidationError` wird am Sender in einen terminalen Konfigurations- oder
Payloadfehler übersetzt. Es gibt keinen stillen Fallback auf Schema `1.0`,
untypisierte Zusatzfelder oder Shared-Secret-only-Transport.

## Gemeinsamer Envelope

Video und Report verwenden dieselben Top-Level-Felder:

| Feld | Bedeutung |
| --- | --- |
| `transfer_key` | Deterministische, bei Retries unveränderte Transferidentität |
| `source_node_key` | Aktiver Sender-Node mit Rolle `site_node` |
| `target_node_key` | Aktiver Empfänger-Node mit Rolle `central_hub` |
| `source_center_key` | Muss dem `owning_center` des Sender-Nodes entsprechen |
| `resource_kind` | Diskriminator `video` oder `report` |
| `resource_hash` | Fachliche Identität des Quellobjekts; muss zum Resource-Row passen |
| `transfer_mode` | Im Produktionspfad `metadata_and_processed_media` |
| `processing_policy` | Aktuell `preserve_processing_state` |
| `processing_intent` | Aktuell `sender_requests_state_preservation` |
| `cleanup_policy` | Konservativer Standard `retain_all` |
| `payload_schema_version` | Literal `3.0` |
| `resource_rows` | Durch `resource_kind` diskriminierter Payload |
| `processing_snapshot` | Aktuell `sender_processing_success: true` |
| `provenance` | Optionale, anonymisierte Transportprovenienz ohne lokale Primärschlüssel |

Lokale Datenbank-IDs, absolute Pfade und Originaldateinamen sind keine
portablen Identitäten und gehören nicht in den Wire-Payload.

## Video-Payload

Ein minimales Processed-Video für Vertrag `3.0` sieht so aus:

```json
{
  "transfer_key": "site_a__video__<resource_sha256>__processed_v1",
  "source_node_key": "site_a",
  "target_node_key": "central_hub",
  "source_center_key": "center_a",
  "resource_kind": "video",
  "resource_hash": "<resource_sha256>",
  "transfer_mode": "metadata_and_processed_media",
  "processing_policy": "preserve_processing_state",
  "processing_intent": "sender_requests_state_preservation",
  "cleanup_policy": "retain_all",
  "payload_schema_version": "3.0",
  "resource_rows": {
    "video_file": {
      "video_hash": "<resource_sha256>",
      "processed_video_hash": "<processed_file_sha256>",
      "suffix": ".mp4",
      "fps": 25.0,
      "duration": 60.0,
      "frame_count": 1500,
      "width": 1280,
      "height": 720
    },
    "sensitive_meta": {
      "patient_hash": "<patient_sha256>",
      "examination_hash": "<examination_sha256>"
    },
    "video_state": {
      "processing_started": true,
      "sensitive_meta_processed": true,
      "anonymized": true,
      "anonymization_validated": true,
      "processed_file_sha256": "<processed_file_sha256>"
    },
    "processing_history": {
      "file_hash": "<resource_sha256>",
      "success": true
    },
    "video_segments": [],
    "frame_annotations": [],
    "reports": []
  },
  "processing_snapshot": {
    "sender_processing_success": true
  }
}
```

Für jedes Segment gelten zusätzlich:

- `source_node_key` und `video_hash` müssen zum Envelope passen;
- `end_frame_number_exclusive` ist exklusiv und größer als
  `start_frame_number`;
- Segmente dürfen die deklarierte `frame_count` nicht überschreiten;
- `source_node_key` plus `source_segment_id` ist innerhalb des Payloads
  eindeutig;
- `model_name` und `model_version` werden nur gemeinsam und nur für
  exportierte Prediction-Segmente übertragen;
- Präsentationszeitstempel bleiben für klinische Identität maßgeblich. Der
  Transfer darf keine Frame-Koordinaten neu berechnen.

Die Video-Storage-Normalisierung bleibt eine zusätzliche Pflichtprüfung. Ein
passender SHA-256-Hash beweist Integrität, aber nicht Codec, Pixel-Format,
Auflösung, Framerate, Bitrate, Bytebudget oder Timeline-Konformität. Maßgeblich
ist [`video_storage_normalization.md`](video_storage_normalization.md).

## Report-Payload

Reports übertragen ausschließlich das anonymisierte Derivat im Portable
Document Format (PDF).

Ein minimales Processed-Report-Beispiel:

```json
{
  "transfer_key": "site_a__report__<resource_sha256>__processed_v1",
  "source_node_key": "site_a",
  "target_node_key": "central_hub",
  "source_center_key": "center_a",
  "resource_kind": "report",
  "resource_hash": "<resource_sha256>",
  "transfer_mode": "metadata_and_processed_media",
  "processing_policy": "preserve_processing_state",
  "processing_intent": "sender_requests_state_preservation",
  "cleanup_policy": "retain_all",
  "payload_schema_version": "3.0",
  "resource_rows": {
    "raw_pdf_file": {
      "pdf_hash": "<resource_sha256>",
      "anonymized_text": "Anonymisierter Berichtstext"
    },
    "sensitive_meta": {
      "patient_hash": "<patient_sha256>",
      "examination_hash": "<examination_sha256>"
    },
    "raw_pdf_state": {
      "processing_started": true,
      "sensitive_meta_processed": true,
      "anonymized": true,
      "anonymization_validated": true,
      "processed_file_sha256": "<processed_file_sha256>"
    },
    "processing_history": {
      "file_hash": "<resource_sha256>",
      "success": true
    },
    "reports": []
  },
  "processing_snapshot": {
    "sender_processing_success": true
  }
}
```

`pdf_hash` bezeichnet die fachliche Ressourcenidentität. Der separate
`processed_file_sha256` bezeichnet exakt die Bytes, die im zweiten Schritt
hochgeladen werden. Der Receiver vergleicht den Upload mit diesem Wert. Das
Feld `text`, direkte Patientendaten, `raw_meta` und Raw-PDF-Bytes sind nicht
Teil des Transfervertrags.

## mTLS-Transporttyp

Der aktuelle Sender benutzt ein explizites, eingefrorenes Transportobjekt:

```python
from dataclasses import dataclass
from typing import TypedDict


class HubTransportRequestKwargs(TypedDict, total=False):
    allow_redirects: bool
    verify: str | bool
    cert: tuple[str, str]


@dataclass(frozen=True)
class HubTransportConfig:
    cert: tuple[str, str] | None
    verify: str | bool
```

Die zugehörigen Sender-Einstellungen sind:

```sh
LX_ANNOTATE_HUB_EXPORT_REQUIRE_MTLS=true
LX_ANNOTATE_HUB_EXPORT_CLIENT_CERT_FILE=/run/secrets/hub-client.crt
LX_ANNOTATE_HUB_EXPORT_CLIENT_KEY_FILE=/run/secrets/hub-client.key
LX_ANNOTATE_HUB_EXPORT_CA_FILE=/run/secrets/hub-ca.crt
```

Bei aktiviertem mTLS müssen Zertifikat und Key gemeinsam vorhanden und lesbar
sein. Ein optionales Bundle der Zertifizierungsstelle (Certificate Authority,
CA) ersetzt `verify=True` durch seinen Pfad, niemals durch `False`. Ziele müssen
`https://` verwenden. Redirects sind deaktiviert,
damit Node-Credentials nicht an ein anderes Ziel weitergereicht werden.

Auf Receiver-Seite bleibt der Proxyvertrag aus
[`deployment_note_hub_contract.md`](deployment_note_hub_contract.md)
verbindlich: vom Client gesetzte Forwarded- und Zertifikatsheader werden
entfernt und nur nach erfolgreicher Proxyprüfung neu gesetzt.

## Zweiphasiger Ablauf und Acknowledgement

1. Sender sperrt beziehungsweise lädt den lokalen Outbound-Job.
2. Sender berechnet den Processed-Media-Hash aus den aktuellen Bytes.
3. Sender baut den Payload und validiert ihn mit dem passenden
   `validate_hub_transfer_*_payload()`-Validator.
4. Sender registriert Metadaten mit demselben deterministischen
   `transfer_key`, auch bei Retry.
5. Nur bei `awaiting_media` lädt er `media_role=processed` hoch.
6. Sender fragt den Status ab und validiert das Acknowledgement gegen seinen
   unveränderlichen lokalen Job.
7. Erst `applied` mit übereinstimmender Gesamtidentität erlaubt `completed`
   oder lokale Cleanup-Eignung.

Das Acknowledgement muss mindestens für folgende Felder übereinstimmen:

- Remote-Transfer-ID und `transfer_key`;
- Source-Node, Target-Node und Source-Center;
- `resource_kind`, `resource_hash` und `processed_media_hash`;
- `transfer_mode` und `payload_schema_version`.

Fehlende oder abweichende Felder sind terminale Integritätsfehler. Sie werden
nicht durch einen neuen Transfer-Key oder ein ungeprüftes Retry kaschiert.

## Portierung von `data-transfer-nginx-mtls`

Beim Übertragen einzelner Ideen aus einem älteren Branch gelten diese
Ersetzungen:

| Alter Ansatz | Aktueller Ansatz |
| --- | --- |
| Eigener `HubTransferClient` in `endoreg_db` | Sender-Workflow in `lx_annotate.hub.hub_export_worker` |
| `verify_tls: bool` plus untypisiertes Kwargs-Dictionary | `HubTransportConfig` und `HubTransportRequestKwargs` mit `verify: str | bool` |
| CLI-Flags mit optionalem Client-Zertifikat | Produktionsprofil verlangt vollständiges mTLS-Material und schlägt sonst vor dem Request fehl |
| Payload-Schema `1.0` | Striktes, diskriminiertes Schema `3.0` aus `lx_dtypes` |
| `dict[str, Any]` bleibt nach Validierung im Umlauf | Validator-Rückgabe ersetzt das ursprüngliche Mapping |
| `ANONYMIZED` oder `sensitive_meta_processed` reicht | Ausschließlich explizit `anonymization_validated=true` |
| Patientendaten zur Hashableitung übertragen | Nur bereits lokal gebildete `patient_hash` und `examination_hash` |
| Reportfelder `text` und `anonymized_text` | Ausschließlich `anonymized_text` |
| Report-Upload ohne separaten Processed-Hash | `raw_pdf_state.processed_file_sha256` ist Pflicht |
| Filesystem-Implementierung unter `endoreg_db.utils.file_operations` | Kanonische Mutation über `endoreg_db.utils.filesystem.file_operations`; bestehende Kompatibilitätsimporte nicht als neue Ownership-Grenze verwenden |
| Django-Session als Transfer-Scope | Authentifizierter `NetworkNode.owning_center` ist alleiniger Machine-to-Machine-Scope |

Der alte Branch soll deshalb nicht vollständig gemergt werden. Portiert werden
nur isolierte Änderungen, die nach Rebase dieselben Typen und Invarianten
erhalten. Insbesondere dürfen keine alten Payload-Builder, Raw-Media-Pfade,
Filesystem-Verschiebungen oder Session-Scope-Annahmen übernommen werden.

## Fehlerbilder

| Fehlermeldung oder Status | Ursache | Korrektur |
| --- | --- | --- |
| `Only privacy-preserving hub payload_schema_version '3.0' is accepted` | Veralteter Sender | Sender und `lx_dtypes` gemeinsam aktualisieren; kein Receiver-Fallback |
| `extra_forbidden` | Altes oder direkt identifizierendes Feld | Feld entfernen oder zuerst im gemeinsamen Vertrag modellieren |
| `anonymization_status=... is not eligible` | Noch nicht explizit validiert | Klinische Anonymisierungsvalidierung abschließen |
| `processed_file_sha256 is required` | Processed-Bytes wurden nicht lokal gehasht | SHA-256 aus dem tatsächlich zu sendenden Artefakt berechnen |
| `source_center_key must match ... owning center` | Node-/Center-Konfiguration widersprüchlich | `NetworkNode.owning_center` und Senderjob korrigieren |
| `inconsistent` bei Replay | Gleicher Key mit anderem kanonischem Payload | Alten Job untersuchen; nur bei neuer fachlicher Identität neuen Key erzeugen |
| `403` trotz Shared Secret | HTTPS-, mTLS- oder Node-Prüfung fehlt | Zertifikat, CA, Proxy-Attestation und Node prüfen; nicht auf Shared-Secret-only zurückfallen |

## Verifikation nach einer Portierung

Im Endoreg-Repository:

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest \
  tests/views/media/test_hub_transfer_endpoints.py \
  tests/services/test_transfer_job_contract.py -q
```

Im lx-annotate-Repository:

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest tests/hub -q
```

Zusätzlich ist ein produktionsnaher Cross-Repository-Test erforderlich:
gültiger Video- und Reporttransfer, fehlendes Zertifikat, falsche CA,
abgelaufenes Zertifikat, falscher Center, Payload `1.0`, Raw-Feld,
Hashabweichung, identischer Replay, veränderter Replay, verlorenes
Acknowledgement und Worker-Neustart.

## Weiterführende Dokumente

- [`hub_ingest_operations.md`](hub_ingest_operations.md)
- [`deployment_note_hub_contract.md`](deployment_note_hub_contract.md)
- [`wiki/hub_ingest_current_state.md`](wiki/hub_ingest_current_state.md)
- [`video_storage_normalization.md`](video_storage_normalization.md)
- `/home/admin/dev/lx-annotate/docs/guides/hub-export-workflow.md`
