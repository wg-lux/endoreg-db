from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from endoreg_db.models import (
    VideoFile, 
    Center, 
    Label, 
    VideoSegmentationLabelSet,
    VideoSegmentationLabel,
    EndoscopyProcessor
)

class Command(BaseCommand):
    help = 'Import fallback test video and create default labels'

    def add_arguments(self, parser):
        parser.add_argument(
            '--video-path',
            type=str,
            default='~/test-data/video/lux-gastro-video.mp4',
            help='Path to the test video file'
        )

    def handle(self, *args, **options):
        video_path_str = options['video_path']
        video_path = Path(video_path_str).expanduser()
        
        self.stdout.write(f"Looking for video at: {video_path}")
        
        if not video_path.exists():
            self.stdout.write(
                self.style.WARNING(f"Video file not found at {video_path}")
            )
            # Create a fallback entry anyway for frontend testing
            self.create_fallback_entries()
            return
        
        # Create or get default center
        center, created = Center.objects.get_or_create(
            name="Default Center",
            defaults={
                'description': 'Fallback center for test videos',
                'location': 'Test Location'
            }
        )
        if created:
            self.stdout.write(f"Created center: {center.name}")
        
        # Create or get default processor
        processor, created = EndoscopyProcessor.objects.get_or_create(
            name="Default Processor",
            defaults={
                'description': 'Fallback processor for test videos'
            }
        )
        if created:
            self.stdout.write(f"Created processor: {processor.name}")
        
        # Create default labels
        self.create_default_labels()
        
        # Import the video if it exists
        try:
            video_file = VideoFile.create_from_file_initialized(
                file_path=video_path,
                center_name=center.name,
                processor_name=processor.name
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully imported video: {video_file.uuid}")
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"Failed to import video: {e}")
            )
            # Create fallback entries for frontend testing
            self.create_fallback_entries()

    def create_default_labels(self):
        """Create default labels for video segmentation"""
        
        # Create default labelset
        labelset, created = VideoSegmentationLabelSet.objects.get_or_create(
            name="Default Endoscopy Labels",
            defaults={
                'description': 'Default labels for endoscopy video annotation'
            }
        )
        if created:
            self.stdout.write(f"Created labelset: {labelset.name}")
        
        # Default labels for endoscopy
        default_labels = [
            {'name': 'outside', 'name_de': 'Außerhalb', 'name_en': 'Outside', 'color': '#00bcd4', 'order_priority': 1},
            {'name': 'appendix', 'name_de': 'Appendix', 'name_en': 'Appendix', 'color': '#ff9800', 'order_priority': 2},
            {'name': 'blood', 'name_de': 'Blut', 'name_en': 'Blood', 'color': '#f44336', 'order_priority': 3},
            {'name': 'diverticule', 'name_de': 'Divertikel', 'name_en': 'Diverticule', 'color': '#9c27b0', 'order_priority': 4},
            {'name': 'grasper', 'name_de': 'Greifer', 'name_en': 'Grasper', 'color': '#CBEDCA', 'order_priority': 5},
            {'name': 'ileocaecalvalve', 'name_de': 'Ileozäkalklappe', 'name_en': 'Ileocaecal Valve', 'color': '#3f51b5', 'order_priority': 6},
            {'name': 'ileum', 'name_de': 'Ileum', 'name_en': 'Ileum', 'color': '#2196f3', 'order_priority': 7},
            {'name': 'low_quality', 'name_de': 'Niedrige Bildqualität', 'name_en': 'Low Quality', 'color': '#9e9e9e', 'order_priority': 8},
            {'name': 'nbi', 'name_de': 'Narrow Band Imaging', 'name_en': 'NBI', 'color': '#795548', 'order_priority': 9},
            {'name': 'needle', 'name_de': 'Nadel', 'name_en': 'Needle', 'color': '#e91e63', 'order_priority': 10},
            {'name': 'polyp', 'name_de': 'Polyp', 'name_en': 'Polyp', 'color': '#8bc34a', 'order_priority': 11},
            {'name': 'snare', 'name_de': 'Snare', 'name_en': 'Snare', 'color': '#ff5722', 'order_priority': 12},
            {'name': 'water_jet', 'name_de': 'Wasserstrahl', 'name_en': 'Water Jet', 'color': '#03a9f4', 'order_priority': 13},
            {'name': 'wound', 'name_de': 'Wunde', 'name_en': 'Wound', 'color': '#607d8b', 'order_priority': 14},
        ]
        
        for label_data in default_labels:
            # Create VideoSegmentationLabel
            vs_label, created = VideoSegmentationLabel.objects.get_or_create(
                name=label_data['name'],
                defaults={
                    'name_de': label_data['name_de'],
                    'name_en': label_data['name_en'],
                    'color': label_data['color'],
                    'order_priority': label_data['order_priority'],
                    'description': f"Default {label_data['name_en']} label"
                }
            )
            
            # Create general Label (for compatibility)
            general_label, created = Label.objects.get_or_create(
                name=label_data['name'],
                defaults={
                    'description': f"Default {label_data['name_en']} label"
                }
            )
            
            if created:
                self.stdout.write(f"Created label: {label_data['name']}")
            
            # Link to labelset
            if vs_label not in labelset.labels.all():
                labelset.labels.add(vs_label)
        
        labelset.save()
        self.stdout.write(f"Labelset configured with {labelset.labels.count()} labels")

    def create_fallback_entries(self):
        """Create fallback entries for testing when video file is not available"""
        
        # Create default labels anyway
        self.create_default_labels()
        
        # Create a placeholder VideoFile entry for frontend testing
        center, _ = Center.objects.get_or_create(
            name="Default Center",
            defaults={
                'description': 'Fallback center for test videos',
                'location': 'Test Location'
            }
        )
        
        processor, _ = EndoscopyProcessor.objects.get_or_create(
            name="Default Processor",
            defaults={
                'description': 'Fallback processor for test videos'
            }
        )
        
        # Check if we already have a fallback video
        if not VideoFile.objects.filter(original_file_name="lux-gastro-video.mp4").exists():
            # Create a minimal VideoFile entry for frontend testing
            video_file = VideoFile.objects.create(
                original_file_name="lux-gastro-video.mp4",
                video_hash="fallback_hash_12345",
                center=center,
                processor=processor,
                fps=30.0,
                duration=120.0,  # 2 minutes fallback
                frame_count=3600,
                width=1920,
                height=1080
            )
            
            # Initialize the video file
            video_file.get_or_create_state()
            video_file.set_frame_dir()
            
            self.stdout.write(
                self.style.SUCCESS(f"Created fallback video entry: {video_file.uuid}")
            )