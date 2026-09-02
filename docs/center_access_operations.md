# Center-Zugriff und zentrale Video-Sichtbarkeit

Dieses Runbook beschreibt den Betriebsvertrag für das Feature
[`center_access`](../feature-tracking/CenterAccess.yml). 

## Begriffe und Identitätsquelle

Center-Zugriff wird ausschließlich aus einer bereits kryptografisch geprüften
Keycloak-Claim `groups` übernommen. Ein Center hat das Gruppenformat
`/centers/<center_key>`, zum Beispiel `/centers/berlin`. Die Claim muss eine
Liste von Strings sein. Verschachtelte Pfade, leere Schlüssel und unbekannte
`center_key`-Werte werden abgewiesen. Eine fehlerhafte Anmeldung verändert die
bisherige lokale Zuordnung nicht.

Die lokale Many-to-many-Beziehung `PortalUserInfo.centers` ist nur ein Cache
der Identitätsquelle. Bei jeder erneuten Anmeldung ersetzt die geprüfte Claim
den Cache vollständig. Null, ein oder mehrere Center sind zulässig. Das im
Workflow konfigurierte Standard-Center gewährt keine Berechtigung. `is_staff`
und `is_superuser` bleiben ausdrückliche globale Ausnahmen; fehlende
Memberships erzeugen niemals implizit globalen Zugriff.

## Zugriffsmatrix

`eigen` bedeutet hier stets: über die geprüfte Membership einem Center
zugeordnet. Ein Standard-Center oder die Deployment-Rolle ersetzt diese
Zuordnung nicht.

| Ressource/Aktion | `standalone` | `site_node` | `local_study_server` | `central_hub` |
|---|---|---|---|---|
| Video-Liste und Anonymisierungsübersicht | eigene Center | eigene Center | eigene Center | eigene Center vollständig; fremde Center nur anonymisierte, verarbeitete Videos mit pseudonymen Metadaten |
| Anonymisiertes, verarbeitetes Playback: HLS-Playlist, Schlüssel, Segment, Frame und Timeline | eigene Center und `video:read` | eigene Center und `video:read` | eigene Center und `video:read` | mit `video:read` hubweit, wenn verarbeitet, anonymisiert, fehlerfrei und nicht `lost` |
| Rohvideo und rohe Frames | eigene Center und `video:read` | eigene Center und `video:read` | eigene Center und `video:read` | eigene Center und `video:read`; keine Hub-Ausnahme |
| Patienten | eigene Center und `patient:read` beziehungsweise `patient:write` | eigene Center und Fachrolle | eigene Center und Fachrolle | eigene Center und Fachrolle; keine Hub-Ausnahme |
| Reports | eigene Center und `patient:read` beziehungsweise `patient:write` | eigene Center und Fachrolle | eigene Center und Fachrolle | eigene Center und Fachrolle; keine Hub-Ausnahme |
| Uploads | eigene Center und `patient:write` | eigene Center und `patient:write` | genau ein erklärtes eigenes Center und `patient:write` | eigene Center und `patient:write`; keine Hub-Ausnahme |
| Annotationsexporte | eigene Center und `video:write` | eigene Center und `video:write` | genau ein eigenes Center oder ausdrücklich global als Staff und `video:write` | eigene Center und `video:write`; Rohmedienexport bleibt verboten |
| Administration und Quarantäne | nur jeweilige Admin-/Fachrolle; Center-Grenze der Zielressource bleibt erhalten | ebenso | ebenso | ebenso; `video:read` gewährt keine Administration |
| Schreiboperationen einschließlich Segmentänderung, Reimport und Export-Flag | eigenes Center und passende `*:write`-Rolle | eigenes Center und passende `*:write`-Rolle | eigenes Center und passende `*:write`-Rolle | eigenes Center und passende `*:write`-Rolle; keine Hub-Leseausnahme |
| Hub-Transfer-Receiver: Registrierung, Status und verarbeitetes Medium | deaktiviert (`404`) | deaktiviert (`404`) | deaktiviert (`404`) | gültige Node-Credentials und mTLS; Center ausschließlich aus `NetworkNode.owning_center`, keine Django-Benutzersitzung |

Ein Hub-Detail für ein fremdes Center enthält insbesondere keine Patientennamen,
Geburtsdaten, Originaldateinamen, lokalen Pfade, Integritätsfehler,
Bearbeiternamen oder Upload-Diagnostik. lx-annotate zeigt dafür eine neutrale
Bezeichnung `Video <id>` und das Center an.

### Technische Durchsetzung und Prüfpunkte

- `endoreg_db.views.access_control` trennt die enge, nur lesende
  Hub-Ausnahme von der strikten Center-Prüfung für Patienten, Rohmedien und
  URL-adressierte Videoschreibpfade. Fremde vorhandene Objekte liefern wie
  nicht vorhandene Objekte `404`, damit kein Ressourcen-Existenz-Orakel
  entsteht.
- Der dokumentierte Debug-Vertrag bleibt konsistent: Wenn
  `EnvironmentAwarePermission` anonyme lokale Debug-Anfragen zulässt, erzeugt
  die nachgelagerte Center-Prüfung keine widersprüchliche Membership-Sperre.
  Dieser Bypass gilt nicht in Produktion.
- Hub-Transfer-Endpunkte sind eine getrennte Machine-to-Machine-Grenze. Sie
  authentifizieren den `NetworkNode` und binden jede Operation an dessen
  `owning_center`; Django-Benutzerrollen oder -Memberships werden dort weder
  benötigt noch ausgewertet.
- `PatientViewSet.get_queryset()` schränkt Liste, Detail, Änderung und Löschung
  auf Memberships ein; die Erstellung prüft den validierten `center_key` vor
  dem Speichern.
- Upload und Annotationsexport verlangen eine Fachrolle. Der Exportservice
  vergleicht zusätzlich Video-Center, optionalen `center_key` und effektive
  Memberships. `all_centers` bleibt eine ausdrückliche Staff-Ausnahme.
- Segmentänderung, Korrektur, Reimport und Export-Freigabe verwenden sowohl
  `video:write` als auch die strikte Center-Prüfung. Die Hub-Playback-Ausnahme
  wird in diesen Pfaden nicht aufgerufen.
- lx-annotate übernimmt Center-Schlüssel und pseudonyme Labels aus der API,
  trifft aber keine Sicherheitsentscheidung; die Autorisierung bleibt im
  Backend.
- `tests/views/test_center_access_matrix.py` prüft die Patientenabgrenzung für
  alle vier Rollen sowie negative Hub-Fälle für Upload, Export, Administration
  und Schreibzugriff. Die spezialisierten View-Tests prüfen Hub-Transfer,
  FHIR, Patientenerstellung, Reports, Rohmedien, Existenzverschleierung und
  verarbeitetes Playback.

## Zuweisen, Entziehen und Aktualisieren

Die Administrationsseite verwaltet die lokale, plural ausgelegte
`PortalUserInfo.centers`-Zuordnung. Django-Superuser dürfen alle Benutzer und
Center verwalten. Die aus Keycloak synchronisierte Rolle
`center_scope:global_admin` gewährt dieselbe globale Center-Verwaltung, ohne
den Benutzer zum Django-Superuser zu machen. `center_scope:admin` bleibt auf
das eine eindeutig zugeordnete eigene Center beschränkt. Breite Rollen wie
`data:write`, `admin` oder ein bloßer Staff-Status reichen nicht aus.

Beide Administratorrechte werden als exakt benannte **Keycloak-Realm-Rollen**
zugewiesen. Für die globale Administrationsansicht wird
`center_scope:global_admin` verwendet; für delegierte Center-Verwaltung
`center_scope:admin`. Die Anwendung synchronisiert diese Rollen bei der
Anmeldung in gleichnamige Django-Gruppen, legt oder entzieht aber selbst keine
Keycloak-Rollen.

Globale Administratoren sehen auf der Administrationsseite zusätzlich alle in
`NetworkNode` registrierten Hosts, einschließlich inaktiver Einträge, Rolle,
Center, URL-/HTTPS-Konfigurationsstatus und Zeitpunkt der letzten lokalen
Änderung. Die Ansicht gibt weder URL noch Shared Secret aus und führt beim
Öffnen keinen Remote-Liveness-Probe aus. Sie zeigt damit bewusst den lokalen
Registrierungs- und Konfigurationsstatus; die Laufzeit-Telemetrie der
Storage-Knoten bleibt separat gekennzeichnet.

Jede Änderung benötigt eine Begründung und einen Konfliktschutz auf Basis der
zuvor gelesenen Center-Schlüssel und wird dauerhaft auditiert. Änderungen des
eigenen Kontos sind verboten. Die API ändert keine Keycloak-Rollen oder
-Gruppen: Bei der nächsten Anmeldung ersetzt die verifizierte
`/centers/<center_key>`-Claim den lokalen Cache. Dauerhafte Zuweisungen müssen
daher zusätzlich in Keycloak vorgenommen werden.

1. Im Identity Provider die Gruppen `/centers/<center_key>` zuweisen oder
   entziehen. Der Schlüssel muss bereits in `Center.center_key` existieren.
2. Die aktive Sitzung beenden und erneut anmelden beziehungsweise den
   OpenID-Connect-Authentifizierungsfluss vollständig erneuern. Ein bloßes
   Neuladen der Seite aktualisiert einen bereits ausgestellten Token nicht.
3. Prüfen, dass das Ereignis `center_access_identity_sync_completed` für die
   erwartete Benutzer-ID und die Center-IDs protokolliert wurde.
4. Bei Entzug zusätzlich prüfen, dass der Benutzer außerhalb der verbleibenden
   Center eine HTTP-403-Antwort erhält.

Tokens, vollständige Claims und klinische Nutzdaten dürfen niemals in Tickets,
Shell-Ausgaben oder Logs kopiert werden.

## Diagnose

Die effektive Konfiguration kann ohne Token-Inhalt geprüft werden:

```bash
devenv shell -- python manage.py shell -c \
  'from django.contrib.auth import get_user_model; from endoreg_db.services.center_access import resolve_allowed_center_ids; from endoreg_db.services.hub import get_deployment_role; u=get_user_model().objects.get(username="BENUTZER"); print({"deployment_role": get_deployment_role(), "user_id": u.pk, "center_ids": sorted(resolve_allowed_center_ids(u) or []) if resolve_allowed_center_ids(u) is not None else "global"})'
```

Relevante strukturierte JSON-Ereignisse:

- `center_access_identity_sync_completed`: Membership-Cache wurde ersetzt.
- `center_access_identity_sync_rejected` mit `malformed_groups_claim`: Claim-Form ist ungültig.
- `center_access_identity_sync_rejected` mit `unknown_center_keys`: Identity Provider und Center-Stammdaten stimmen nicht überein.
- `center_access_denied` mit `no_membership`: außerhalb der Hub-Leseausnahme fehlt eine Zuordnung.
- `center_access_denied` mit `outside_center_scope`: Ressource liegt in einem anderen Center.
- `center_access_denied` mit `hub_video_not_anonymized_processed`: eine Hub-Anfrage zielte auf ein unvollständiges, fehlerhaftes oder verlorenes Video.

Bei leerer Video-Liste zuerst Rolle `video:read`, Deployment-Rolle und erneute
Anmeldung prüfen. Bei verbotenem Playback zusätzlich sicherstellen, dass ein
verarbeitetes Artefakt vorhanden ist, `VideoState.anonymized` gesetzt ist,
`processing_error` nicht gesetzt ist und `meta.integrity_status` nicht `lost`
lautet. Unbekannte Center-Claims werden in Keycloak oder in den bewusst
ausgerollten Center-Stammdaten korrigiert; es gibt keinen automatischen
Fallback auf das Standard-Center.

## Migration, Rollout und Rücknahme

Die Migration `0051_portaluserinfo_centers` legt die pluralen Memberships an
und kopiert bestehende `Examiner.center`-Zuordnungen vorwärts. Vor dem Rollout
werden Datenbanksicherung, Migrationsplan und die Anzahl bestehender
`PortalUserInfo`-Zeilen dokumentiert. Die Rücknahme erfolgt durch Anwendung der
vorherigen Applikationsversion und Django-Migration auf den vorherigen Stand;
die alte `Examiner.center`-Beziehung bleibt während der Übergangsphase erhalten.
Eine Rückmigration entfernt neue Mehrfachzuordnungen und darf daher nur nach
gesicherter Export-/Wiederherstellungsprüfung ausgeführt werden.

Rollout-Reihenfolge:

1. `./feature-tracking/tracker.py validate` und die fokussierten Tests aus dem
   Tracker ausführen.
2. Migration zunächst in einer produktionsähnlichen Umgebung vorwärts und
   rückwärts testen; Zeilenzahlen und Legacy-Zugriff vergleichen.
3. `ENDOREG_DEPLOYMENT_ROLE` explizit prüfen. Hubweite Sichtbarkeit ist nur bei
   `central_hub` zulässig.
4. Alle betroffenen Backend-Prozesse neu starten, damit Settings und Code
   einheitlich geladen sind; danach eine neue Anmeldung erzwingen.
5. Je einen positiven Hub-Fall und einen negativen Site-Node-, Rohmedien- und
   Schreibfall prüfen. Erst dann die Betriebsbewertung im Tracker aktualisieren.

Bei unerwarteter Sichtbarkeit wird zuerst die Anwendungsversion zurückgenommen
oder die Deployment-Rolle von `central_hub` entfernt und anschließend jeder
Backend-Prozess neu gestartet. Membership-Daten werden nicht automatisch
gelöscht. Rohmedien werden weder exportiert noch als Wiederherstellungsfallback
verwendet.
