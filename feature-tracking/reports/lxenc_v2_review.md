# LXENC-v2-Nachprüfung, 2026-11-02

Prüfer: `codex-boot-safety-20261102`, mit getrennten Python- und Rust-Reviews.
Umfang: Dateiformat, Bereichsleser und Rotation in Claudes Arbeitsstand.
Maßgebliche Abnahme: `StorageSecurity.yml`, Kriterium `encrypted_at_rest`.
Diese Prüfung ist keine Produktionsfreigabe oder Prüfung der Datenträger.

## Behobene Befunde

1. **Hoch – Endprüfung bei frühen Bereichen fehlte.** Ein 96-Byte-Payload
   mit 32-Byte-Chunks lieferte nach Entfernung des letzten vollständigen
   Records weiterhin Byte 0. Dadurch konnten beschädigte Dateien über
   Bereichsabfragen unbemerkt teilweise ausgeliefert werden. Python und Rust
   authentifizieren jetzt den Endrecord vor Rückgabe eines Bereichs.
   Stellen: `endoreg_db/utils/encryption/encryption.py:596` und
   `rust/endoreg_rust_backend/src/encrypted_range.rs:169`.
   Regression: Entfernen, Anhängen und Tag-Manipulation bei früher Abfrage.

2. **Hoch – Bereichsantwort konnte Dateigenerationen vermischen.** Nach der
   Layoutprüfung öffnete Storage den Pfad je nativem Batch erneut. Ein atomarer
   Austausch konnte eine Antwort aus unterschiedlichen Dateien erzeugen.
   `endoreg_db/utils/encryption/encrypted.py:197` hält jetzt für alle
   Schlüsselkonfigurationen einen gemeinsamen Deskriptor. Der Regressionstest
   ersetzt die Datei während einer Antwort über der bisherigen Batchgröße.
   Storage verwendet dabei den Python-Leser; ein Durchsatzvergleich steht aus.

3. **Mittel – Legacy-Dateien mit aktivem Schlüssel wurden nicht migriert.**
   Rotation übersprang diese Dateien auch bei explizitem Apply.
   `endoreg_db/utils/encryption/rotation.py:101` überspringt jetzt nur v2-Dateien
   mit aktivem Schlüssel. Der Test prüft unveränderte Vorschau, Upgrade und
   unveränderten Wiederholungslauf. Staging-Prüfung und atomare Veröffentlichung
   bleiben erhalten.

## Nachprüfung: Kompatibilität und gemeinsame Implementierung

Die öffentliche native Headerfunktion hatte ihre Rückgabe von sechs auf acht
Felder geändert. Die sechsfeldrige Schnittstelle ist wiederhergestellt;
`parse_encrypted_header_metadata` liefert zusätzliche Kennungen über denselben
Parser. Ein unpassendes natives Modul wird vor neuen Schreibvorgängen mit einer
konkreten Installationsmeldung abgewiesen. Python und natives Modul gemeinsam
ausliefern; v1-Lesekompatibilität ist keine v2-Lesefähigkeit alter Software.

Storage, Reparatur und Rotation verwenden denselben Encoder `encrypt_chunks`.
Python verwendet jetzt `encrypted_chunk_context` aus `encrypted_format.rs` für
Chunk-Geometrie, Nonces und authentifizierte Daten statt eigener Kopien dieser
Regeln. Native Bereichs- und Hashleser nutzen dieselben Rust-Helfer. I/O-Schleifen,
Layoutberechnung und Bibliotheksaufrufe bleiben plattformspezifisch; gemeinsame
Testdateien prüfen deren Übereinstimmung. Ein vollständiger Leistungsbenchmark
wurde nicht durchgeführt. HLS-Schlüssel und Transferhüllen bleiben separate
Protokolle und werden nicht mit LXENC zusammengelegt.

Der unabhängige v1-Testencoder verwendet eingefrorene Formatkonstanten. Die
Kompatibilitätsmatrix umfasst 16-/24-/32-Byte-Schlüssel, aktive und auslaufende
Generationen, leere Dateien sowie volle und teilweise letzte Chunks. Zusätzliche
Tests prüfen die alte API, falsche native Versionen, Zählerüberlauf und den bisher
unlesbaren v1-Sonderfall eines leeren Records: dieser wird vor Ausgabe abgewiesen.

## Betriebsgrenzen

Vor Apply alle Leser auf v2 aktualisieren und eine unabhängig geprüfte,
verschlüsselte Sicherung samt Schlüsseln behalten. Ein Software-Rollback muss
bereits geschriebene v2-Dateien weiterhin lesen können. Anleitung:
[`secret_rotation.md`](../../docs/secret_rotation.md#format-versions).

V1 benötigt weiterhin einen unabhängigen Soll-Hash gegen früheren Verlust ganzer
Chunks. `file_id` bindet den Hüllschlüssel, aber noch keinen Datenbankdatensatz:
der Austausch vollständiger gültiger Dateien ist dadurch allein nicht erkennbar.
Bereichsleser prüfen den Endrecord und angeforderte Records, nicht alle
übersprungenen Mittelstücke. Ein partiell konsumierter sequenzieller Stream ist
keine vollständige Dateiprüfung. Es wurden keine Produktivdaten migriert.

## Verifikation

- `devenv shell -- bash -c 'cargo fmt --manifest-path rust/endoreg_rust_backend/Cargo.toml --check && RUSTFLAGS="$(python3-config --embed --ldflags)" cargo test --offline --manifest-path rust/endoreg_rust_backend/Cargo.toml --lib'`: 37 bestanden.
- `devenv shell -- maturin develop --release --offline`: native Erweiterung gebaut und lokal installiert.
- `devenv shell -- .devenv/state/venv/bin/pytest tests/utils/test_encryption_format_v2.py tests/utils/test_encryption_range.py tests/utils/test_encryption_streaming.py tests/rust_backend/test_encrypted_contract.py tests/services/test_secret_rotation.py -q`: 97 bestanden, eine Plugin-Warnung; zusätzlich Aufräumwarnungen für alte pytest-Verzeichnisse.
- `devenv shell -- env RUN_VIDEO_TESTS=true .devenv/state/venv/bin/pytest tests/views/video/test_video_stream.py tests/views/report/test_report_stream.py tests/utils/test_storage_materialization.py tests/services/test_report_materialization_contracts.py tests/services/test_streamable_media_contract.py -q -rs`: 32 bestanden, 2 Warnungen; zusätzlich dieselben Aufräumwarnungen.
- `.devenv/state/venv/bin/pyright` auf den drei geänderten Verschlüsselungsmodulen und den drei geänderten Python-Testdateien: 0 Fehler, 0 Warnungen; Ruff und Formatprüfung bestanden.
- Vollständiger Pyright: 4 bestehende Fehler in `tests/dataloader/test_load_base_db_data_repeat.py`, 2 Warnungen außerhalb des Umfangs. Fremde Änderungen erhalten.
- Direkter pytest-Start ohne Devenv scheiterte an fehlendem `libpq`; der obige Devenv-Lauf löst die Umgebung korrekt auf.
- Strukturprüfung: 67 EndoReg- und 23 LuxNix-Manifeste gültig. LuxNix-System-Python fehlte PyYAML; Wiederholung mit dem Projekt-venv erfolgreich.
- Tracker-Validierung: 40 Definitionen gültig. `tracker.py check storage_security`: Exit 1, weiterhin nicht produktionsreif (2/8).
