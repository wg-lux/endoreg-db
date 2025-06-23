from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from typing import List, TYPE_CHECKING
if TYPE_CHECKING:
    from .patient_examination import PatientExamination
    from ..finding import (
        Finding,
    )
    from .patient_finding_location import PatientFindingLocation
    from .patient_finding_morphology import PatientFindingMorphology
    from .patient_finding_intervention import PatientFindingIntervention


import random
class PatientFinding(models.Model):
    patient_examination = models.ForeignKey('PatientExamination', on_delete=models.CASCADE, related_name='patient_findings')
    finding = models.ForeignKey('Finding', on_delete=models.CASCADE, related_name='finding_patient_findings')
    
    # Audit-Felder für medizinische Nachverfolgung
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='created_findings', null=True, blank=True)
    updated_by = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='updated_findings', null=True, blank=True)
    
    # Soft Delete für historische Daten
    is_active = models.BooleanField(default=True, help_text="Deaktiviert statt gelöscht für Audit-Trail")
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='deactivated_findings', null=True, blank=True)
    
    if TYPE_CHECKING:
        patient_examination: "PatientExamination"
        finding: "Finding"
        locations: models.QuerySet["PatientFindingLocation"]
        morphologies: models.QuerySet["PatientFindingMorphology"]
        interventions: models.QuerySet["PatientFindingIntervention"]

    class Meta:
        verbose_name = 'Patient Finding'
        verbose_name_plural = 'Patient Findings'
        ordering = ['patient_examination', 'finding']
        
        # Wichtige Constraints für Datenintegrität
        constraints = [
            models.UniqueConstraint(
                fields=['patient_examination', 'finding'],
                condition=models.Q(is_active=True),
                name='unique_active_finding_per_examination'
            ),
            models.CheckConstraint(
                check=models.Q(
                    deactivated_at__isnull=True,
                    deactivated_by__isnull=True
                ) | models.Q(
                    deactivated_at__isnull=False,
                    deactivated_by__isnull=False,
                    is_active=False
                ),
                name='deactivation_fields_consistency'
            )
        ]
        
        # Performance-optimierte Indizes
        indexes = [
            models.Index(fields=['patient_examination', 'finding']),
            models.Index(fields=['patient_examination', 'is_active']),
            models.Index(fields=['created_at']),
            models.Index(fields=['finding', 'is_active']),
        ]

    @property
    def patient(self):
        """Returns the patient associated with this patient finding."""
        return self.patient_examination.patient

    def __str__(self):
        status = " (deaktiviert)" if not self.is_active else ""
        return f"{self.patient_examination} - {self.finding}{status}"

    def clean(self):
        """Model-Level Validierung für Geschäftslogik"""
        super().clean()
        
        # Prüfe ob Finding für diese Examination erlaubt ist
        if self.finding and self.patient_examination:
            available_findings = self.patient_examination.examination.get_available_findings()
            if self.finding not in available_findings:
                raise ValidationError({
                    'finding': f'Finding "{self.finding.name}" ist nicht für Examination "{self.patient_examination.examination.name}" erlaubt.'
                })
        
        # Prüfe Required Findings Logic
        if self.finding and self.patient_examination:
            self._validate_required_findings()
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
    def _validate_required_findings(self):
        """Validiert Required vs Optional Finding Constraints"""
        examination = self.patient_examination.examination
        
        # Hole Required Findings für diese Examination
        required_findings = getattr(examination, 'required_findings', None)
        if required_findings and required_findings.exists():
            # Prüfe ob alle Required Findings vorhanden sind
            existing_findings = self.patient_examination.patient_findings.filter(
                is_active=True
            ).values_list('finding', flat=True)
            
            missing_required = required_findings.exclude(id__in=existing_findings)
            if missing_required.exists() and self.finding not in required_findings.all():
                missing_names = ', '.join([f.name for f in missing_required])
                raise ValidationError(
                    f'Erforderliche Findings fehlen: {missing_names}'
                )

    def deactivate(self, user=None, reason=None):
        """Soft Delete mit Audit-Trail"""
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.deactivated_by = user
        self.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by'])
        
        # Deaktiviere auch abhängige Objekte
        self.locations.update(is_active=False, deactivated_at=timezone.now())
        self.morphologies.update(is_active=False, deactivated_at=timezone.now())
        self.interventions.update(is_active=False, deactivated_at=timezone.now())

    def reactivate(self, user=None):
        """Reaktivierung mit Validation"""
        if not self.is_active:
            # Prüfe ob Reaktivierung erlaubt ist
            try:
                self.clean()
                self.is_active = True
                self.deactivated_at = None
                self.deactivated_by = None
                self.updated_by = user
                self.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by', 'updated_by'])
            except ValidationError as e:
                raise ValidationError(f'Reaktivierung nicht möglich: {e}')

    # Optimierte Query-Methoden
    def get_locations(self):
        """Returns all active location choices that are associated with this patient finding."""
        return self.locations.filter(is_active=True).select_related(
            'location_classification', 'location_choice'
        )
    
    def get_morphologies(self):
        """Returns all active morphology choices that are associated with this patient finding."""
        return self.morphologies.filter(is_active=True).select_related(
            'morphology_classification', 'morphology_choice'
        )

    def get_interventions(self):
        """Returns all active interventions that are associated with this patient finding."""
        return self.interventions.filter(is_active=True).select_related('intervention')

    # Verbesserte Add-Methoden mit Validierung
    def add_location(self, location_classification_id, location_choice_id, user=None):
        """Adds a validated location choice to this patient finding."""
        from .patient_finding_location import PatientFindingLocation
        from ..finding import FindingLocationClassification
        
        try:
            location_classification = FindingLocationClassification.objects.get(
                id=location_classification_id
            )
            
            # Validiere dass Choice zur Classification gehört
            if not location_classification.choices.filter(id=location_choice_id).exists():
                raise ValidationError(
                    f'Location Choice {location_choice_id} gehört nicht zu Classification {location_classification_id}'
                )
            
            # Prüfe ob Location bereits existiert
            existing = self.locations.filter(
                location_classification_id=location_classification_id,
                location_choice_id=location_choice_id,
                is_active=True
            ).first()
            
            if existing:
                return existing
            
            patient_finding_location = PatientFindingLocation.objects.create(
                finding=self,
                location_classification_id=location_classification_id,
                location_choice_id=location_choice_id,
                created_by=user
            )
            
            return patient_finding_location
            
        except FindingLocationClassification.DoesNotExist:
            raise ValidationError(f'Location Classification {location_classification_id} nicht gefunden')

    def add_morphology(self, morphology_classification_id, morphology_choice_id, user=None):
        """Adds a validated morphology choice to this patient finding."""
        from .patient_finding_morphology import PatientFindingMorphology
        from ..finding import FindingMorphologyClassification
        
        try:
            morphology_classification = FindingMorphologyClassification.objects.get(
                id=morphology_classification_id
            )
            
            # Validiere dass Choice zur Classification gehört
            if not morphology_classification.choices.filter(id=morphology_choice_id).exists():
                raise ValidationError(
                    f'Morphology Choice {morphology_choice_id} gehört nicht zu Classification {morphology_classification_id}'
                )
            
            # Prüfe Duplikate
            existing = self.morphologies.filter(
                morphology_classification_id=morphology_classification_id,
                morphology_choice_id=morphology_choice_id,
                is_active=True
            ).first()
            
            if existing:
                return existing
            
            patient_finding_morphology = PatientFindingMorphology.objects.create(
                finding=self,
                morphology_classification_id=morphology_classification_id,
                morphology_choice_id=morphology_choice_id,
                created_by=user
            )
            
            return patient_finding_morphology
            
        except FindingMorphologyClassification.DoesNotExist:
            raise ValidationError(f'Morphology Classification {morphology_classification_id} nicht gefunden')

    def add_intervention(self, intervention_id, state="pending", date=None, user=None):
        """Adds a validated intervention to this patient finding."""
        from .patient_finding_intervention import PatientFindingIntervention
        from ..finding import FindingIntervention
        
        try:
            intervention = FindingIntervention.objects.get(id=intervention_id)
            
            patient_finding_intervention = PatientFindingIntervention.objects.create(
                patient_finding=self,
                intervention=intervention,
                state=state,
                date=date or timezone.now(),
                created_by=user
            )
            
            return patient_finding_intervention
            
        except FindingIntervention.DoesNotExist:
            raise ValidationError(f'Intervention {intervention_id} nicht gefunden')

    # Legacy Methoden für Backward Compatibility (deprecated)
    def add_morphology_choice(self, morphology_choice, morphology_classification):
        """DEPRECATED: Use add_morphology() instead"""
        import warnings
        warnings.warn("add_morphology_choice is deprecated, use add_morphology", DeprecationWarning)
        return self.add_morphology(morphology_classification.id, morphology_choice.id)

    def add_location_choice(self, location_choice, location_classification):
        """DEPRECATED: Use add_location() instead"""
        import warnings
        warnings.warn("add_location_choice is deprecated, use add_location", DeprecationWarning)
        return self.add_location(location_classification.id, location_choice.id)

    def set_random_location(self, location_classification):
        """Sets a random location for this finding based on the location classification."""
        location_choices = location_classification.choices.all()
        if not location_choices.exists():
            raise ValidationError(f'Keine Choices für Location Classification {location_classification.name}')
            
        location_choice = random.choice(location_choices)
        return self.add_location(location_classification.id, location_choice.id)

    def add_video_segment(self, video_segment):
        """Add video segment to finding"""
        self.video_segments.add(video_segment)
        return video_segment

    # Manager für active/inactive Objekte
    @property
    def active_locations(self):
        return self.locations.filter(is_active=True)
    
    @property 
    def active_morphologies(self):
        return self.morphologies.filter(is_active=True)
    
    @property
    def active_interventions(self):
        return self.interventions.filter(is_active=True)
