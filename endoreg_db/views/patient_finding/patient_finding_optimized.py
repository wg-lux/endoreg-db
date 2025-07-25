from endoreg_db.models import Examination, Finding, FindingClassification, PatientFinding
from endoreg_db.serializers.patient_finding import PatientFindingClassificationSerializer, PatientFindingDetailSerializer, PatientFindingListSerializer, PatientFindingWriteSerializer


from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


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

        # Detail-Views: fetch nested relations
        if self.action in ['retrieve', 'update', 'partial_update']:
            return base_queryset.prefetch_related(
                "classifications__classification",
                "classifications__choice",
                # 'locations__location_classification',
                # 'locations__location_choice',
                # 'morphologies__morphology_classification', 
                # 'morphologies__morphology_choice',
                'interventions__intervention'
            )

        # For List-Views: fetch only necessary relations
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
                # 'name_de': examination.name_de,
                # 'name_en': examination.name_en,
            },
            'findings': []
        }

        for finding in findings:
            assert isinstance(finding, Finding), "Expected Finding instance"
            finding_data = {
                'id': finding.id,
                'name': finding.name,
                # 'name_de': finding.name_de,
                # 'name_en': finding.name_en,
                "classifications": [],
                'location_classifications': [],
                'morphology_classifications': []
            }

            for classification_obj in finding.get_classifications():
                assert isinstance(classification_obj, FindingClassification), "Expected FindingClassification instance"
                classification_data = {
                    'id': classification_obj.id,
                    'name': classification_obj.name,
                    "choices": [
                        {
                            'id': choice.id,
                            'name': choice.name,
                        }
                        for choice in classification_obj.choices.all()
                    ]
                }

                finding_data['classifications'].append(classification_data)

    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """
        Bulk-Endpoint für gleichzeitige Erstellung mehrerer PatientFindings
        Optimiert für Mobile Apps mit schlechter Verbindung
        """
        findings_data = request.data.get('findings', [])
        if not findings_data:
            return Response(
                {'error': 'findings array required'},
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
    def add_classification(self, request, pk=None):
        """
        Create and link a PatientFindingClassification to the PatientFinding

        Called by: POST /api/patient-findings/{id}/add-classification/ #TODO CHECK

        Expected payload: {
            "classification_id": 1,
            "choice_id": 2

        """
        patient_finding = self.get_object()
        assert isinstance(patient_finding, PatientFinding), "Expected PatientFinding instance"
        classification_data = request.data

        # Validierung
        classification_id = classification_data.get('classification_id')
        choice_id = classification_data.get('choice_id')

        if not classification_id or not choice_id:
            return Response(
                {'error': 'classification und choice erforderlich'},
                status=status.HTTP_400_BAD_REQUEST
            )

        patient_finding.add_classification(classification_id, choice_id)
        try:
            patient_finding_classification = patient_finding.add_classification(classification_id, choice_id)
            serializer = PatientFindingClassificationSerializer(patient_finding_classification)
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
            assert isinstance(finding, PatientFinding), "Expected PatientFinding instance"
            base_data = {
                'finding_id': finding.id,
                'patient_id': finding.patient_examination.patient.id,
                'patient_name': finding.patient_examination.patient.get_full_name(),
                'examination_type': finding.patient_examination.examination.name,
                'examination_date': finding.patient_examination.date_start,
                'finding_name': finding.finding.name,
            }

            # Locations denormalisieren
            for classification in finding.classifications.all():
                classification_data = base_data.copy()
                classification_data.update({
                    'location_classification': classification.location_classification.name,
                    'location_choice': classification.location_choice.name,
                    'location_subcategories': classification.subcategories,
                })
                export_data.append(classification_data)

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