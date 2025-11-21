* Patientendaten: Eindeutige Identifikation des Patienten (z.B. Name, Geburtsdatum, Patienten-ID).
* Indikation: Der klinische Grund für die Durchführung der Endoskopie muss klar dokumentiert sein.
* Untersucher und Assistenz: Namentliche Nennung des verantwortlichen ärztlichen und des assistierenden Fachpersonals.
* Verwendetes Gerät: Exakte Angabe des Endoskoptyps sowie der eindeutigen Geräteidentifikation (Seriennummer).
* Datum (und Uhrzeit) des Untersuchungs-Beginns und -endes: Diese Zeitstempel sind essenziell für die Prozessdokumentation und die Abrechnung.


Zeit Dokumentation
Zeitpunkt	Bedeutung und Notwendigkeit
E1: Patient betritt Untersuchungsraum	Start der Personal- und Raumbelegung.
E2: Beginn der Endoskopie	Zeitpunkt, an dem das Gerät in die Körperöffnung eingeführt wird.
E3: Beginn Rückzug des Endoskops	Pflichtangabe: Essentiell zur Qualitätssicherung und Berechnung der Rückzugszeit.
E4: Ende der Endoskopie	Zeitpunkt, an dem das Gerät aus der Körperöffnung entfernt wird.

- Zökumintubation (bool) - CAVE: Bei operiertem Situs nicht unbedingt sinnvoll, im Verlauf eher "vollständig" definieren und aus automatischer Dokumentatino der Visualisierten Dokumente ableiten
- Ileum Intubation (bool) - s.o.

- Darmvorbereitung (z.B. Boston Bowel Preparation Scale - BBPS)

Der Befundbericht muss eine detaillierte Beschreibung aller pathologischen Befunde enthalten, einschließlich Lokalisation, Größe und Morphologie. Ein unauffälliger Befund ist explizit zu dokumentieren.

* Paris-Klassifikation: Dient der morphologischen Beurteilung der Wuchsform und ist entscheidend für die Einschätzung der initialen Resektabilität.
* NICE-Klassifikation (NBI International Colorectal Endoscopic Classification): Ermöglicht mittels virtueller Chromoendoskopie eine optische Differenzierung zwischen hyperplastischen und adenomatösen Polypen in Echtzeit.
* J-NET-Klassifikation (Japan NBI Expert Team): Erlaubt eine detaillierte Beurteilung von Gefäß- und Oberflächenmustern zur präziseren Einschätzung des Malignitätspotenzials und der Invasionstiefe, was die Wahl der Resektionstechnik (z.B. EMR vs. ESD) maßgeblich beeinflusst.

---
coloreg_colonoscopy_requirements:

- patient_data:
    - patient_id: true - skip for now
    - patient_first_name: true
    - patient_last_name: true
    - patient_birth_date: true
    - previous bowel_surgery (cat yes no unknown)
    - last_known_colonoscopy_date (None, unknown or date)
- examination_data
    - examination
    - examination_indication (for now with categories "screening", "symptomatic", "planned_resection", "follow_up", "surveillance", "other", "unknown")
    - sedation (for now as finding with categories "propofol", "midazolam", "none", "other", "unknown")
    - Times: ExaminationStart, WithdrawalStart, ExaminationEnd
    - bowel prep (BBPS)
    - deepest_intubatino (new finding, maps to colon_location)
- findings_data (focus on polyp for now):
    - location (colonoscopy_default)
        - if location is rectum or sigmoid, location_cm is also required
    - size
        - preferred: size_mm, if not available, size_categorical
    - morphology:
        - polyps < 10 mm: paris_classification
        - polyps >= 10 mm: paris_classification, nice_classification
        - polyps >= 20 mm: LST_classification, nice_classification
    - intervention
        - biopsy / resection?
        - clip?

