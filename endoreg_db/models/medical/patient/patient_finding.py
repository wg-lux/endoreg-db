from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from endoreg_db.models import (
        Finding,
        LabelVideoSegment,
        PatientExamination,
        PatientFindingClassification,
        PatientFindingIntervention,
    )
    from endoreg_db.utils.links.requirement_link import RequirementLinks


class PatientFinding(models.Model):
    patient_examination = models.ForeignKey("PatientExamination", on_delete=models.CASCADE, related_name="patient_findings")
    finding = models.ForeignKey("Finding", on_delete=models.CASCADE, related_name="finding_patient_findings")

    # Audit-Felder für medizinische Nachverfolgung
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.PROTECT, related_name="created_findings", null=True, blank=True)
    updated_by = models.ForeignKey("auth.User", on_delete=models.PROTECT, related_name="updated_findings", null=True, blank=True)

    # Soft Delete für historische Daten
    is_active = models.BooleanField(default=True, help_text="Deaktiviert statt gelöscht für Audit-Trail")
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey("auth.User", on_delete=models.PROTECT, related_name="deactivated_findings", null=True, blank=True)

    if TYPE_CHECKING:
        patient_examination: models.ForeignKey["PatientExamination"]
        finding: models.ForeignKey["Finding"]

        @property
        def video_segments(self) -> models.manager.RelatedManager["LabelVideoSegment"]: ...

        @property
        def interventions(self) -> models.manager.RelatedManager["PatientFindingIntervention"]: ...

        @property
        def classifications(self) -> models.manager.RelatedManager["PatientFindingClassification"]: ...

    class Meta:
        verbose_name = "Patient Finding"
        verbose_name_plural = "Patient Findings"
        ordering = ["patient_examination", "finding"]

        # Wichtige Constraints für Datenintegrität
        constraints = [
            models.UniqueConstraint(
                fields=["patient_examination", "finding"], condition=models.Q(is_active=True), name="unique_active_finding_per_examination"
            ),
            models.CheckConstraint(
                check=models.Q(  # called .check in future?
                    deactivated_at__isnull=True, deactivated_by__isnull=True
                )
                | models.Q(deactivated_at__isnull=False, deactivated_by__isnull=False, is_active=False),
                name="deactivation_fields_consistency",
            ),
        ]

        # Performance-optimierte Indizes
        indexes = [
            models.Index(fields=["patient_examination", "finding"]),
            models.Index(fields=["patient_examination", "is_active"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["finding", "is_active"]),
        ]

    @property
    def patient(self):
        """Returns the patient associated with this patient finding."""
        return self.patient_examination.patient

    def __str__(self):
        status = " (deaktiviert)" if not self.is_active else ""
        return f"{self.patient_examination} - {self.finding}{status}"

    def clean(self):
        """
        Validates the patient finding against business rules before saving.

        Ensures that the selected finding is allowed for the associated examination and that all required findings are present. Raises a ValidationError if the finding is not permitted or required findings are missing.
        """
        super().clean()

        # Prüfe ob Finding für diese Examination erlaubt ist
        if self.finding and self.patient_examination:
            available_findings = self.patient_examination.examination_safe.get_available_findings()
            if self.finding not in available_findings:
                raise ValidationError(
                    {"finding": f'Finding "{self.finding.name}" ist nicht für Examination "{self.patient_examination.examination_safe.name}" erlaubt.'}
                )

        # Prüfe Required Findings Logic
        if self.finding and self.patient_examination:
            self._validate_required_findings()

    # This avoids validation errors on partial updates
    def save(self, *args, **kwargs):
        """
        Validates the model instance before saving, unless performing a partial update.

        Performs full model validation with `full_clean()` before saving, except when `update_fields` is specified for a partial update.
        """
        if not kwargs.get("update_fields"):
            self.full_clean()
        super().save(*args, **kwargs)

    def _validate_required_findings(self):
        """Validiert Required vs Optional Finding Constraints"""
        examination = self.patient_examination.examination

        # Hole Required Findings für diese Examination
        required_findings = getattr(examination, "required_findings", None)
        if required_findings and required_findings.exists():
            # Prüfe ob alle Required Findings vorhanden sind
            existing_findings = self.patient_examination.patient_findings.filter(is_active=True).values_list("finding", flat=True)

            missing_required = required_findings.exclude(id__in=existing_findings)
            if missing_required.exists() and self.finding not in required_findings.all():
                missing_names = ", ".join([f.name for f in missing_required])
                raise ValidationError(f"Erforderliche Findings fehlen: {missing_names}")

    def deactivate(self, user=None, reason=None):
        """Soft Delete mit Audit-Trail"""
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.deactivated_by = user
        self.save(update_fields=["is_active", "deactivated_at", "deactivated_by"])

        # Deaktiviere auch abhängige Objekte
        self.locations.update(is_active=False, deactivated_at=timezone.now())
        self.morphologies.update(is_active=False, deactivated_at=timezone.now())
        self.interventions.update(is_active=False, deactivated_at=timezone.now())

    def reactivate(self, user=None):
        """
        Reactivates a previously deactivated patient finding after validating its state.

        If validation passes, sets the finding as active, clears deactivation fields, updates the user, and saves changes. Raises a ValidationError if reactivation is not allowed.
        """
        if not self.is_active:
            # Prüfe ob Reaktivierung erlaubt ist
            try:
                self.clean()
                self.is_active = True
                self.deactivated_at = None
                self.deactivated_by = None
                self.updated_by = user
                self.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_by"])
            except ValidationError as e:
                raise ValidationError(f"Reaktivierung nicht möglich: {e}")

    def get_interventions(self):
        """
        Retrieve all active interventions associated with this patient finding.

        Returns:
            QuerySet: Active related interventions with intervention details prefetched.
        """
        return self.interventions.filter(is_active=True).select_related("intervention")

    def add_classification(
        self,
        classification_id,
        classification_choice_id,
        user=None,
    ) -> "PatientFindingClassification":
        """
        Add a classification choice to this patient finding after validating its association.

        Parameters:
            classification_id: The ID of the classification to add.
            classification_choice_id: The ID of the classification choice to associate.
            user: Optional user performing the action.

        Returns:
            PatientFindingClassification: The created or existing active classification association.

        Raises:
            ValidationError: If the classification does not exist or the choice is not valid for the classification.
        """
        from ..finding import FindingClassification, FindingClassificationChoice
        from .patient_finding_classification import PatientFindingClassification

        try:
            classification = FindingClassification.objects.get(id=classification_id)
            classification_choice = FindingClassificationChoice.objects.filter(id=classification_choice_id).first()

            if not classification.choices.filter(id=classification_choice_id).exists():
                raise ValidationError(f"Classification Choice {classification_choice_id} gehört nicht zu Classification {classification_id}")

            existing = self.classifications.filter(classification=classification, classification_choice=classification_choice, is_active=True).first()

            if existing:
                return existing

            patient_finding_classification = PatientFindingClassification.objects.create(
                finding=self,
                classification_id=classification_id,
                classification_choice_id=classification_choice_id,
            )

            return patient_finding_classification

        except FindingClassification.DoesNotExist:
            raise ValidationError(f"Classification {classification_id} nicht gefunden")

    def add_intervention(self, intervention_id, state="pending", date=None, user=None):
        """
        Add an intervention to the patient finding after validating its existence.

        Parameters:
            intervention_id (int): The ID of the intervention to add.
            state (str, optional): The state of the intervention. Defaults to "pending".
            date (datetime, optional): The date of the intervention. Defaults to the current time if not provided.
            user (User, optional): The user creating the intervention.

        Returns:
            PatientFindingIntervention: The created intervention instance.

        Raises:
            ValidationError: If the specified intervention does not exist.
        """
        from ..finding import FindingIntervention
        from .patient_finding_intervention import PatientFindingIntervention

        try:
            intervention = FindingIntervention.objects.get(id=intervention_id)

            patient_finding_intervention = PatientFindingIntervention.objects.create(
                patient_finding=self, intervention=intervention, state=state, date=date or timezone.now(), created_by=user
            )

            return patient_finding_intervention

        except FindingIntervention.DoesNotExist:
            raise ValidationError(f"Intervention {intervention_id} nicht gefunden")

    def add_video_segment(self, video_segment):
        """
        Associates a video segment with this patient finding.

        Parameters:
                video_segment: The video segment instance to add.

        Returns:
                The added video segment instance.
        """
        self.video_segments.add(video_segment)  # TODO
        return video_segment

    # Manager für active/inactive Objekte
    @property
    def active_classifications(self):
        """
        Return all active classifications associated with this patient finding.

        Returns:
                QuerySet: Active related classifications where `is_active` is True.
        """
        return self.classifications.filter(is_active=True)

    @property
    def locations(self):
        """
        Return all classifications of this patient finding that are of type "location".

        Returns:
                QuerySet: Classifications related to this finding filtered by the "location" classification type.
        """
        classifications = self.classifications.filter(classification__classification_types__name__iexact="location")
        return classifications

    @property
    def morphologies(self):
        """
        Return all classifications of this patient finding that are of type "morphology".

        Returns:
                QuerySet: Classifications related to this finding filtered by the "morphology" classification type.
        """
        classifications = self.classifications.filter(classification__classification_types__name__iexact="morphology")
        return classifications

    @property
    def active_interventions(self):
        return self.interventions.filter(is_active=True)

    @property
    def links(self) -> "RequirementLinks":
        """
        Aggregates and returns all related model instances relevant for requirement evaluation
        as a RequirementLinks object.

        This property provides access to:
        - The finding associated with this patient finding
        - All active finding classifications and their choices
        - All active finding interventions
        - The patient examination and patient
        """
        from typing import List, cast

        from endoreg_db.utils.links.requirement_link import RequirementLinks

        # Get the base finding
        findings_list = [self.finding] if self.finding else []

        # Get all active finding classifications and their choices
        finding_classifications_list = []
        finding_classification_choices_list = []

        for pf_classification in self.active_classifications:
            if pf_classification.classification:
                finding_classifications_list.append(pf_classification.classification)
            if pf_classification.classification_choice:
                finding_classification_choices_list.append(pf_classification.classification_choice)

        # Get all active finding interventions
        finding_interventions_list = []
        for pf_intervention in self.active_interventions:
            if pf_intervention.intervention:
                finding_interventions_list.append(pf_intervention.intervention)

        # Include patient examination and patient for context
        patient_examinations_list = [self.patient_examination] if self.patient_examination else []
        patient_findings_list = cast("List[PatientFinding]", [self])  # Include self for direct patient finding evaluations

        return RequirementLinks(
            findings=findings_list,
            finding_classifications=finding_classifications_list,
            finding_classification_choices=finding_classification_choices_list,
            finding_interventions=finding_interventions_list,
            patient_examinations=patient_examinations_list,
            patient_findings=patient_findings_list,
        )
