# Lokale Identitäts-Keyrings

Vertrag: [Anonymization.yml](../feature-tracking/Anonymization.yml),
Kriterium `central_identity_salts_disabled`. Patientenidentität bleibt lokal;
siehe [Transfergrenze](source_patient_matching.md).

Zentrale Bereitstellung und Verteilung von Identitätssalts sind deaktiviert.
LuxNix darf keine Zentrumssalts erzeugen oder Leserechte darauf vergeben.
`centerIdentitySalt` und `identitySaltViaSecretSpec` werden bei Aktivierung
abgewiesen. Der bisherige zentrale Launcher und Provisioner brechen vor
Geheimniszugriff beziehungsweise Netzwerkkommunikation ab. Der Backend-Loader
weist `DJANGO_IDENTITY_SALT_BUNDLE` und den Modus `secretspec` zurück.

Bestehende lokale `DJANGO_SALT_FILE`- und
`DJANGO_IDENTITY_SALT_KEYRING_FILE`-Konfigurationen bleiben unterstützt. Alle
lokalen Identitätsschreiber müssen dieselben etablierten Generationen verwenden.
Fehlende Schlüssel dürfen nicht automatisch neu erzeugt oder durch zentral
bezogene Werte ersetzt werden. Historische Generationen bleiben für lokale
Wiedererkennung erhalten, bis eine geprüfte Migration ihre Entfernung erlaubt.

Bei `Configured identity salt does not match legacy patient hashes` bestehende
Schlüssel und Datensätze erhalten. Die ursprüngliche lokale Generation anhand
geprüfter Wiederherstellungsunterlagen klären; weder Hashes löschen noch einen
neuen Salt erzeugen, um den Fehler zu umgehen. Die Verfahren in
[secret_rotation.md](secret_rotation.md) bleiben getrennte, explizite Migrationen.

Die Quellcodeänderung aktiviert keine Hosts und widerruft keine bereits
verteilten Geheimnisse. Vor einem koordinierten Rollout müssen bisher zentral
versorgte Hosts auf ihre erhaltenen lokalen Schlüssel geprüft werden. Keine
automatische Löschung, Rotation oder Umschlüsselung ist Teil dieser Sperre.
