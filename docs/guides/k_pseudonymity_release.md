# Kontrollierte `(k, l, t)`-Release-Ansicht

Diese Funktion erstellt aus einer bereits de-identifizierten CSV-Tabelle eine
separate, kontrollierte Release-Ansicht. Die klinische Ursprungstabelle wird
nicht verändert. Ein Ausgabe-CSV wird nur geschrieben, wenn der vollständig
konfigurierte Freigabe-Prädikat erfüllt ist.

## Gesamtbild

```text
de-identifizierte Ursprungstabelle
+-- feste QI-Definition
+-- feste Sensitive-Attribute
+-- feste Utility-Metriken und Schwellen
    |
    +-- Frequenzprüfung (k)
    +-- optionale l-Diversität
    +-- optionale TV-basierte t-Closeness
    +-- JSD-/Wasserstein-basierte Utility-Prüfung
        |
        +-- erfüllt: Release-CSV + geschütztes Auditmanifest
        +-- nicht erfüllt: kein Release-CSV, Auditmanifest mit Ablehnungsgrund
```

Das Ergebnis ist eine Häufigkeitseigenschaft der veröffentlichten Tabelle. Es
ist kein Nachweis klassischer Personenanonymität. Synthetische Zeilen dürfen
nur dann zu `k` beitragen, wenn Empfänger ihre Provenienz weder sehen noch
zuverlässig ableiten können. Diese Governance-Annahme muss ausdrücklich in der
Konfiguration bestätigt werden.

## Konfiguration

```yaml
schema_version: "1.0"

release_columns:
  - center
  - age_band
  - sex
  - examination_month
  - diagnosis_group
  - procedure_duration_minutes

quasi_identifiers:
  - center
  - age_band
  - sex
  - examination_month

sensitive_attributes:
  - name: diagnosis_group
    allowed_values:
      - benign
      - premalignant
      - malignant
    l_diversity: 2
    t_closeness: 0.20

utility_features:
  - name: diagnosis_group
    kind: categorical
    weight: 0.6
  - name: procedure_duration_minutes
    kind: continuous
    weight: 0.4
    normalization_scale: 60.0

k: 5
tau_max: 0.08
max_synthetic_rows: 500
max_state_evaluations: 10000
max_candidate_combinations: 10000
max_input_rows: 100000

synthetic_rows_count_toward_k: true
recipient_can_observe_synthetic_provenance: false
include_projection_diagnostics: true

repair_cost_weights:
  size: 1.0
  sensitive_changes: 1.0
  distribution: 1.0
```

Direkte Identifikatoren wie Namen, Geburtsdatum, Fallnummer oder externe
Patienten-ID sind in `release_columns` verboten. Nicht deklarierte
Eingabespalten werden nicht in die Release-Ansicht übernommen.

Die Gewichte der `utility_features` müssen exakt zu `1.0` summieren.
Die `allowed_values` bilden den vorab festgelegten endlichen Wertebereich eines
sensitiven Attributes. Werte außerhalb dieser Domäne führen zum Abbruch;
kontinuierliche sensitive Werte müssen vor dem Lauf fachlich gebinnt werden.
Kategorische Features verwenden die Jensen-Shannon-Divergenz mit Logarithmus
zur Basis 2. Kontinuierliche Features verwenden die 1-Wasserstein-Distanz,
geteilt durch die fachlich vorab festgelegte `normalization_scale`.

## Ausführung

```bash
devenv shell -- python manage.py build_k_pseudonymous_release \
  release_policy.yaml \
  deidentified_study_table.csv \
  --release-output /geschuetzter/pfad/release.csv \
  --audit-output /nur-fuer-kustoden/audit.json
```

Beide Dateien werden atomar mit Modus `0600` geschrieben. Bei einer nicht
erfüllten Freigabeprüfung wird ein eventuell vorhandenes altes Release-CSV
entfernt. Das geschützte Auditmanifest bleibt erhalten und dokumentiert:

- Konfiguration und Schwellen;
- Anfangs- und Endzustand des Freigabe-Prädikats;
- vollständige QI-Klassen und optionale Projektionsdiagnostik;
- `k`-Defizite, l-Diversität und TV-basierte t-Closeness;
- gewichtete JSD-/Wasserstein-Utility-Abweichungen;
- Anzahl, Anteil und interne Zeilenpositionen synthetischer Datensätze;
- kanonische SHA-256-Bindungen von Ursprungs- und Release-Tabelle;
- Abbruchgrund und Zahl der untersuchten Zustände.

Die synthetischen Zeilenpositionen erscheinen ausschließlich im geschützten
Manifest. Sie werden nicht als Empfängerfeld in das Release-CSV geschrieben.

## Sicherheits- und Interpretationsgrenzen

- Die Suche ist begrenzt und heuristisch. `no_release` bedeutet nicht, dass
  mathematisch keine zulässige Tabelle existiert, sondern nur, dass im
  erlaubten Suchpfad keine gefunden wurde.
- Realzeilen sind unveränderlich. Reparaturen ergänzen ausschließlich explizit
  synthetische Zeilen aus einem endlichen, durch die Ursprungsdaten bestimmten
  Wertebereich.
- Die Referenzverteilung für t-Closeness und Utility bleibt die initiale reale,
  de-identifizierte Tabelle; synthetische Zeilen verschieben den Maßstab nicht.
- Synthetische Beobachtungen dürfen nicht als reale Patientenzahlen oder
  klinische Auditereignisse interpretiert werden.
- Das Verfahren ersetzt weder Zugriffskontrolle noch Verschlüsselung,
  Empfängermodell, Datenschutz-Folgenabschätzung oder fachliche Freigabe.
