"""
Django Management Command für Frame-Anonymisierung

Dieser Command ermöglicht es, Frame-Anonymisierung über die Kommandozeile zu starten
und zu verwalten. Nützlich für Batch-Processing und automatisierte Workflows.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
import json

from endoreg_db.models import VideoFile, LabelVideoSegment, FrameAnonymizationRequest
from endoreg_db.tasks.frame_anonymization_tasks import process_frame_anonymization
from endoreg_db.utils.frame_anonymization_utils import (
    validate_anonymization_request,
    get_anonymization_statistics,
    cleanup_expired_tokens
)


class Command(BaseCommand):
    help = 'Anonymize video frames based on segments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--video-id',
            type=int,
            help='ID of the video to process'
        )
        
        parser.add_argument(
            '--segment-ids',
            type=str,
            help='Comma-separated list of segment IDs to process'
        )
        
        parser.add_argument(
            '--anonymization-level',
            type=str,
            choices=['minimal', 'faces', 'full'],
            default='faces',
            help='Level of anonymization to apply (default: faces)'
        )
        
        parser.add_argument(
            '--output-format',
            type=str,
            choices=['jpg', 'jpeg', 'png', 'webp'],
            default='jpg',
            help='Output format for anonymized frames (default: jpg)'
        )
        
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run anonymization asynchronously via Celery'
        )
        
        parser.add_argument(
            '--list-requests',
            action='store_true',
            help='List all anonymization requests'
        )
        
        parser.add_argument(
            '--status',
            type=str,
            help='Check status of anonymization request by request ID'
        )
        
        parser.add_argument(
            '--cleanup-expired',
            action='store_true',
            help='Clean up expired download tokens'
        )
        
        parser.add_argument(
            '--statistics',
            action='store_true',
            help='Show anonymization statistics'
        )
        
        parser.add_argument(
            '--batch-file',
            type=str,
            help='JSON file with batch anonymization requests'
        )

    def handle(self, *args, **options):
        """Hauptbehandlungslogik für den Command."""
        
        # Cleanup-Operation
        if options['cleanup_expired']:
            self.handle_cleanup()
            return
        
        # Statistiken anzeigen
        if options['statistics']:
            self.handle_statistics(options.get('video_id'))
            return
        
        # Request-Status prüfen
        if options['status']:
            self.handle_status_check(options['status'])
            return
        
        # Alle Requests auflisten
        if options['list_requests']:
            self.handle_list_requests()
            return
        
        # Batch-Verarbeitung
        if options['batch_file']:
            self.handle_batch_processing(options['batch_file'], options)
            return
        
        # Einzelne Anonymisierung
        if options['video_id'] and options['segment_ids']:
            self.handle_single_anonymization(options)
            return
        
        # Hilfe anzeigen wenn keine spezifische Aktion
        self.stdout.write(self.style.WARNING('No action specified. Use --help for available options.'))

    def handle_single_anonymization(self, options):
        """Behandelt eine einzelne Anonymisierungsanfrage."""
        try:
            video_id = options['video_id']
            segment_ids = [int(id.strip()) for id in options['segment_ids'].split(',')]
            anonymization_level = options['anonymization_level']
            output_format = options['output_format']
            is_async = options['async']
            
            self.stdout.write(f"Starting anonymization for video {video_id}, segments {segment_ids}")
            
            # Validierung
            validation_result = validate_anonymization_request(
                video_id, segment_ids, anonymization_level, output_format
            )
            
            # Video und Segmente prüfen
            try:
                video = VideoFile.objects.get(id=video_id)
                segments = LabelVideoSegment.objects.filter(
                    id__in=segment_ids,
                    video_file=video
                )
                
                if not segments.exists():
                    raise CommandError(f"No valid segments found for video {video_id}")
                
            except VideoFile.DoesNotExist:
                raise CommandError(f"Video with ID {video_id} not found")
            
            # Request erstellen
            request_obj = FrameAnonymizationRequest.objects.create(
                video_file=video,
                segment_ids=segment_ids,
                anonymization_level=anonymization_level,
                output_format=validation_result['normalized_format'],
                status='pending',
                created_at=timezone.now()
            )
            
            self.stdout.write(
                self.style.SUCCESS(f"Created anonymization request: {request_obj.request_id}")
            )
            
            if is_async:
                # Asynchrone Verarbeitung starten
                task = process_frame_anonymization.delay(
                    request_obj.request_id,
                    video_id,
                    segment_ids,
                    anonymization_level,
                    validation_result['normalized_format']
                )
                
                self.stdout.write(
                    self.style.SUCCESS(f"Started async task: {task.id}")
                )
                self.stdout.write("Use --status {request_id} to check progress")
                
            else:
                # Synchrone Verarbeitung
                self.stdout.write("Processing frames synchronously...")
                
                result = process_frame_anonymization(
                    request_obj.request_id,
                    video_id,
                    segment_ids,
                    anonymization_level,
                    validation_result['normalized_format']
                )
                
                self.stdout.write(
                    self.style.SUCCESS(f"Anonymization completed: {result}")
                )
                
        except Exception as e:
            raise CommandError(f"Anonymization failed: {str(e)}")

    def handle_batch_processing(self, batch_file, options):
        """Behandelt Batch-Verarbeitung aus JSON-Datei."""
        try:
            batch_path = Path(batch_file)
            if not batch_path.exists():
                raise CommandError(f"Batch file not found: {batch_file}")
            
            with open(batch_path, 'r') as f:
                batch_data = json.load(f)
            
            if not isinstance(batch_data, list):
                raise CommandError("Batch file must contain a list of requests")
            
            self.stdout.write(f"Processing {len(batch_data)} batch requests...")
            
            results = []
            for i, request_data in enumerate(batch_data):
                try:
                    # Standard-Optionen mit Request-Daten überschreiben
                    request_options = options.copy()
                    request_options.update(request_data)
                    
                    self.stdout.write(f"Processing request {i+1}/{len(batch_data)}")
                    
                    # Einzelne Anonymisierung ausführen
                    self.handle_single_anonymization(request_options)
                    results.append({'index': i, 'status': 'success'})
                    
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"Request {i+1} failed: {str(e)}")
                    )
                    results.append({'index': i, 'status': 'failed', 'error': str(e)})
            
            # Zusammenfassung
            successful = len([r for r in results if r['status'] == 'success'])
            failed = len([r for r in results if r['status'] == 'failed'])
            
            self.stdout.write(
                self.style.SUCCESS(f"Batch processing completed: {successful} successful, {failed} failed")
            )
            
        except Exception as e:
            raise CommandError(f"Batch processing failed: {str(e)}")

    def handle_status_check(self, request_id):
        """Prüft den Status einer Anonymisierungsanfrage."""
        try:
            request_obj = FrameAnonymizationRequest.objects.get(request_id=request_id)
            
            self.stdout.write(f"Request ID: {request_obj.request_id}")
            self.stdout.write(f"Status: {request_obj.status}")
            self.stdout.write(f"Video: {request_obj.video_file.filename}")
            self.stdout.write(f"Segments: {request_obj.segment_ids}")
            self.stdout.write(f"Anonymization Level: {request_obj.anonymization_level}")
            self.stdout.write(f"Output Format: {request_obj.output_format}")
            self.stdout.write(f"Created: {request_obj.created_at}")
            
            if request_obj.started_at:
                self.stdout.write(f"Started: {request_obj.started_at}")
            
            if request_obj.completed_at:
                self.stdout.write(f"Completed: {request_obj.completed_at}")
                
                if request_obj.result_data:
                    self.stdout.write("Results:")
                    for key, value in request_obj.result_data.items():
                        self.stdout.write(f"  {key}: {value}")
            
            if request_obj.error_message:
                self.stdout.write(
                    self.style.ERROR(f"Error: {request_obj.error_message}")
                )
            
            # Anonymisierte Frames anzeigen
            from endoreg_db.models import AnonymousFrame
            frames = AnonymousFrame.objects.filter(
                anonymization_request=request_obj
            ).count()
            
            if frames > 0:
                self.stdout.write(f"Anonymized Frames: {frames}")
                
        except FrameAnonymizationRequest.DoesNotExist:
            raise CommandError(f"Request with ID {request_id} not found")

    def handle_list_requests(self):
        """Listet alle Anonymisierungsanfragen auf."""
        requests = FrameAnonymizationRequest.objects.all().order_by('-created_at')
        
        if not requests.exists():
            self.stdout.write("No anonymization requests found")
            return
        
        self.stdout.write(f"Found {requests.count()} anonymization requests:")
        self.stdout.write("")
        
        for request in requests[:20]:  # Nur die letzten 20 anzeigen
            status_color = self.style.SUCCESS if request.status == 'completed' else (
                self.style.WARNING if request.status == 'processing' else 
                self.style.ERROR if request.status == 'failed' else ''
            )
            
            self.stdout.write(
                f"{request.request_id} | {status_color(request.status.upper())} | "
                f"Video: {request.video_file.filename} | "
                f"Level: {request.anonymization_level} | "
                f"Created: {request.created_at.strftime('%Y-%m-%d %H:%M')}"
            )
        
        if requests.count() > 20:
            self.stdout.write(f"... and {requests.count() - 20} more")

    def handle_cleanup(self):
        """Führt Cleanup-Operationen aus."""
        self.stdout.write("Starting cleanup of expired tokens...")
        
        cleaned_tokens = cleanup_expired_tokens()
        
        self.stdout.write(
            self.style.SUCCESS(f"Cleaned up {cleaned_tokens} expired download tokens")
        )

    def handle_statistics(self, video_id=None):
        """Zeigt Anonymisierungsstatistiken an."""
        stats = get_anonymization_statistics(video_id)
        
        if 'error' in stats:
            raise CommandError(f"Error getting statistics: {stats['error']}")
        
        self.stdout.write("Frame Anonymization Statistics")
        self.stdout.write("=" * 40)
        
        if video_id:
            self.stdout.write(f"Video ID: {video_id}")
            self.stdout.write("")
        
        # Request-Statistiken
        self.stdout.write("Requests:")
        req_stats = stats.get('requests', {})
        for key, value in req_stats.items():
            self.stdout.write(f"  {key.replace('_', ' ').title()}: {value}")
        
        self.stdout.write("")
        
        # Frame-Statistiken
        self.stdout.write("Frames:")
        frame_stats = stats.get('frames', {})
        for key, value in frame_stats.items():
            self.stdout.write(f"  {key.replace('_', ' ').title()}: {value}")
        
        self.stdout.write("")
        
        # Storage-Statistiken
        self.stdout.write("Storage:")
        storage_stats = stats.get('storage', {})
        for key, value in storage_stats.items():
            if 'size' in key:
                unit = 'MB' if 'mb' in key else 'Bytes'
                self.stdout.write(f"  {key.replace('_', ' ').title()}: {value} {unit}")
            else:
                self.stdout.write(f"  {key.replace('_', ' ').title()}: {value}")