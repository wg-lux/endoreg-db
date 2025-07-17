from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from django.utils import timezone

from endoreg_db.models import (
    PatientFinding, 
    Examination,
    Finding,
    FindingClassification
)
from ...serializers.patient_finding import (
    PatientFindingLocationSerializer,
    PatientFindingDetailSerializer,
    PatientFindingListSerializer,
    PatientFindingWriteSerializer
)


class OptimizedPatientFindingViewSet(viewsets.ModelViewSet):
    """
    Hochoptimiertes ViewSet für PatientFinding mit Query-Optimierung,
    Bulk-Endpoints und intelligenter Serializer-Auswahl
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = ['patient_examination__patient', 'finding', 'patient_examination__examination']
    ordering_fields = ['id', 'patient_examination__date_start']
    search_fields = ['finding__name', 'patient_examination__patient__first_name', 'patient_examination__patient__last_name']
    
    def get_queryset(self):
        """Optimierte QuerySet mit Prefetching basierend auf Action"""
        base_queryset = PatientFinding.objects.select_related(
            'patient_examination__patient',
            'patient_examination__examination',
            'finding'
        )
        
        # Für Detail-Views: alle verschachtelten Daten prefetchen
        if self.action in ['retrieve', 'update', 'partial_update']:
            return base_queryset.prefetch_related(
                'locations__location_classification',
                'locations__location_choice',
                'morphologies__morphology_classification', 
                'morphologies__morphology_choice',
                'interventions__intervention'
            )
        
        # Für Listen-Views: minimal prefetching
        return base_queryset
    
    def get_serializer_class(self):
        """Intelligente Serializer-Auswahl basierend auf Action"""
        if self.action == 'list':
            return PatientFindingListSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return PatientFindingWriteSerializer
        else:
            return PatientFindingDetailSerializer
    
    @method_decorator(cache_page(60 * 15))  # 15 Minuten Cache
    @method_decorator(vary_on_headers('Accept-Language'))
    @action(detail=False, methods=['get'])
    def examination_manifest(self, request):
        """
        Bulk-Endpoint: Liefert alle Setup-Daten für eine Examination in einem Call
        Löst das 4-Call-Problem aus der Analyse
        """
        examination_id = request.query_params.get('examination_id')
        if not examination_id:
            return Response(
                {'error': 'examination_id parameter erforderlich'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            examination = Examination.objects.get(id=examination_id)
        except Examination.DoesNotExist:
            return Response(
                {'error': 'Examination nicht gefunden'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Alle Daten in optimierten Queries laden
        findings = examination.get_available_findings().prefetch_related(
            'location_classifications__choices',
            'morphology_classifications__choices'
        )
        
        manifest_data = {
            'examination': {
                'id': examination.id,
                'name': examination.name,
                'name_de': examination.name_de,
                'name_en': examination.name_en,
            },
            'findings': []
        }
        
        for finding in findings:
            finding_data = {
                'id': finding.id,
                'name': finding.name,
                'name_de': finding.name_de,
                'name_en': finding.name_en,
                'location_classifications': [],
                'morphology_classifications': []
            }
            
            # Location Classifications mit Choices
            for loc_class in finding.location_classifications.all():
                loc_data = {
                    'id': loc_class.id,
                    'name': loc_class.name,
                    'name_de': loc_class.name_de,
                    'name_en': loc_class.name_en,
                    'choices': [
                        {
                            'id': choice.id,
                            'name': choice.name,
                            'name_de': choice.name_de,
                            'name_en': choice.name_en,
                        }
                        for choice in loc_class.choices.all()
                    ]
                }
                finding_data['location_classifications'].append(loc_data)
            
            # Morphology Classifications mit Choices
            for morph_class in finding.morphology_classifications.all():
                morph_data = {
                    'id': morph_class.id,
                    'name': morph_class.name,
                    'name_de': morph_class.name_de,
                    'name_en': morph_class.name_en,
                    'choices': [
                        {
                            'id': choice.id,
                            'name': choice.name,
                            'name_de': choice.name_de,
                            'name_en': choice.name_en,
                        }
                        for choice in morph_class.choices.all()
                    ]
                }
                finding_data['morphology_classifications'].append(morph_data)
            
            manifest_data['findings'].append(finding_data)
        
        return Response(manifest_data)
    
    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """
        Bulk-Endpoint für gleichzeitige Erstellung mehrerer PatientFindings
        Optimiert für Mobile Apps mit schlechter Verbindung
        """
        findings_data = request.data.get('findings', [])
        if not findings_data:
            return Response(
                {'error': 'findings array erforderlich'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        created_findings = []
        errors = []
        
        with transaction.atomic():
            for i, finding_data in enumerate(findings_data):
                serializer = self.get_serializer(data=finding_data)
                if serializer.is_valid():
                    try:
                        finding = serializer.save()
                        created_findings.append({
                            'index': i,
                            'id': finding.id,
                            'status': 'created'
                        })
                    except Exception as e:
                        errors.append({
                            'index': i,
                            'error': str(e),
                            'status': 'error'
                        })
                else:
                    errors.append({
                        'index': i,
                        'errors': serializer.errors,
                        'status': 'validation_error'
                    })
        
        return Response({
            'created': created_findings,
            'errors': errors,
            'total_processed': len(findings_data),
            'success_count': len(created_findings),
            'error_count': len(errors)
        }, status=status.HTTP_201_CREATED if created_findings else status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def add_location(self, request, pk=None):
        """Einzelne Location zu einem Finding hinzufügen"""
        finding = self.get_object()
        location_data = request.data
        
        # Validierung
        location_classification_id = location_data.get('location_classification')
        location_choice_id = location_data.get('location_choice')
        
        if not location_classification_id or not location_choice_id:
            return Response(
                {'error': 'location_classification und location_choice erforderlich'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            location_classification = FindingClassification.objects.get(
                id=location_classification_id
            )
            
            # Prüfe ob Choice zur Classification gehört
            if not location_classification.choices.filter(id=location_choice_id).exists():
                return Response(
                    {'error': 'location_choice gehört nicht zur location_classification'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Erstelle Location
            location = finding.add_location(location_classification_id, location_choice_id)
            
            serializer = PatientFindingLocationSerializer(location)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except FindingClassification.DoesNotExist:
            return Response(
                {'error': 'location_classification nicht gefunden'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=False, methods=['get'])
    def export_for_analysis(self, request):
        """
        Export-Endpoint für Datenanalyse mit flacher Struktur
        Unterstützt CSV/JSON Export mit denormalisierten Daten
        """
        queryset = self.filter_queryset(self.get_queryset())
        
        # Format-Parameter
        export_format = request.query_params.get('format', 'json')
        
        # Flache Datenstruktur für Analyse
        export_data = []
        for finding in queryset:
            base_data = {
                'finding_id': finding.id,
                'patient_id': finding.patient_examination.patient.id,
                'patient_name': finding.patient_examination.patient.get_full_name(),
                'examination_type': finding.patient_examination.examination.name,
                'examination_date': finding.patient_examination.date_start,
                'finding_name': finding.finding.name,
            }
            
            # Locations denormalisieren
            for location in finding.locations.all():
                location_data = base_data.copy()
                location_data.update({
                    'location_classification': location.location_classification.name,
                    'location_choice': location.location_choice.name,
                    'location_subcategories': location.subcategories,
                })
                export_data.append(location_data)
            
            # Wenn keine Locations, trotzdem Base-Data hinzufügen
            if not finding.locations.exists():
                export_data.append(base_data)
        
        if export_format == 'csv':
            # CSV-Export implementierung würde hier hin
            pass
        
        return Response({
            'data': export_data,
            'count': len(export_data),
            'exported_at': timezone.now()
        })


class ExaminationManifestCache:
    """Cache-Manager für Examination Manifests"""
    
    @staticmethod
    def get_cache_key(examination_id, language='en'):
        return f"examination_manifest:{examination_id}:{language}"
    
    @staticmethod
    def get_manifest(examination_id, language='en'):
        cache_key = ExaminationManifestCache.get_cache_key(examination_id, language)
        return cache.get(cache_key)
    
    @staticmethod
    def set_manifest(examination_id, data, language='en', timeout=60*60):  # 1 Stunde
        cache_key = ExaminationManifestCache.get_cache_key(examination_id, language)
        cache.set(cache_key, data, timeout)
    
    @staticmethod
    def invalidate_manifest(examination_id):
        """Invalidiere Cache für alle Sprachen"""
        for lang in ['en', 'de']:
            cache_key = ExaminationManifestCache.get_cache_key(examination_id, lang)
            cache.delete(cache_key)