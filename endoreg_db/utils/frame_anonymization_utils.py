"""
Utility-Funktionen für Frame-Anonymisierung

Diese Datei enthält Hilfsfunktionen für die Frame-Anonymisierung,
einschließlich Bildverarbeitung, Pfad-Management und Sicherheitsfunktionen.
"""

import hashlib
import secrets
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

logger = logging.getLogger(__name__)

# Unterstützte Ausgabeformate
SUPPORTED_OUTPUT_FORMATS = ['jpg', 'jpeg', 'png', 'webp']

# Anonymisierungsgrade
ANONYMIZATION_LEVELS = ['minimal', 'faces', 'full']

# Maximale Dateigröße für Frames (in MB)
MAX_FRAME_SIZE_MB = 50


def validate_anonymization_request(video_id: int, segment_ids: List[int], 
                                 anonymization_level: str, output_format: str) -> Dict:
    """
    Validiert eine Frame-Anonymisierungsanfrage.
    
    Args:
        video_id: Video-ID
        segment_ids: Liste der Segment-IDs
        anonymization_level: Anonymisierungsgrad
        output_format: Ausgabeformat
    
    Returns:
        Dict mit Validierungsergebnis
    
    Raises:
        ValidationError: Bei ungültigen Parametern
    """
    errors = []
    
    # Anonymisierungsgrad validieren
    if anonymization_level not in ANONYMIZATION_LEVELS:
        errors.append(f"Invalid anonymization level: {anonymization_level}. "
                     f"Supported levels: {', '.join(ANONYMIZATION_LEVELS)}")
    
    # Ausgabeformat validieren
    if output_format.lower() not in SUPPORTED_OUTPUT_FORMATS:
        errors.append(f"Invalid output format: {output_format}. "
                     f"Supported formats: {', '.join(SUPPORTED_OUTPUT_FORMATS)}")
    
    # Segment-IDs validieren
    if not segment_ids or len(segment_ids) == 0:
        errors.append("At least one segment ID must be provided")
    
    if len(segment_ids) > 100:  # Reasonable limit
        errors.append("Too many segments requested (max 100)")
    
    # Video-ID validieren
    if video_id <= 0:
        errors.append("Invalid video ID")
    
    if errors:
        raise ValidationError(errors)
    
    return {
        'valid': True,
        'normalized_format': output_format.lower(),
        'segment_count': len(segment_ids)
    }


def generate_secure_token(length: int = 32) -> str:
    """
    Generiert einen sicheren Token für Downloads.
    
    Args:
        length: Token-Länge
    
    Returns:
        Sicherer Token
    """
    return secrets.token_urlsafe(length)


def create_secure_download_url(frame_id: int, expiry_hours: int = 24) -> Dict:
    """
    Erstellt eine sichere Download-URL für einen anonymisierten Frame.
    
    Args:
        frame_id: Frame-ID
        expiry_hours: Gültigkeitsdauer in Stunden
    
    Returns:
        Dict mit URL und Token-Informationen
    """
    token = generate_secure_token()
    expiry_time = timezone.now() + timedelta(hours=expiry_hours)
    
    # Token hashen für Datenbank-Speicherung
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    
    return {
        'token': token,
        'token_hash': token_hash,
        'expires_at': expiry_time,
        'frame_id': frame_id
    }


def validate_secure_token(token: str, frame_id: int) -> bool:
    """
    Validiert einen sicheren Download-Token.
    
    Args:
        token: Download-Token
        frame_id: Frame-ID
    
    Returns:
        True wenn Token gültig ist
    """
    try:
        from endoreg_db.models import AnonymousFrame
        
        # Token hashen
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        # Frame mit Token finden
        frame = AnonymousFrame.objects.filter(
            id=frame_id,
            download_token_hash=token_hash,
            download_expires_at__gt=timezone.now()
        ).first()
        
        return frame is not None
        
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}")
        return False


def get_frame_file_info(frame_path: str) -> Optional[Dict]:
    """
    Holt Dateiinformationen für einen Frame.
    
    Args:
        frame_path: Pfad zum Frame
    
    Returns:
        Dict mit Dateiinformationen oder None
    """
    try:
        path = Path(frame_path)
        
        if not path.exists():
            return None
        
        stat = path.stat()
        
        return {
            'filename': path.name,
            'size_bytes': stat.st_size,
            'size_mb': round(stat.st_size / (1024 * 1024), 2),
            'created_at': datetime.fromtimestamp(stat.st_ctime),
            'modified_at': datetime.fromtimestamp(stat.st_mtime),
            'extension': path.suffix.lower(),
            'exists': True
        }
        
    except Exception as e:
        logger.error(f"Error getting file info for {frame_path}: {str(e)}")
        return None


def calculate_anonymization_progress(total_frames: int, processed_frames: int, 
                                   failed_frames: int) -> Dict:
    """
    Berechnet den Fortschritt der Anonymisierung.
    
    Args:
        total_frames: Gesamtzahl der Frames
        processed_frames: Anzahl verarbeiteter Frames
        failed_frames: Anzahl fehlgeschlagener Frames
    
    Returns:
        Dict mit Fortschrittsinformationen
    """
    if total_frames == 0:
        return {
            'progress_percent': 0,
            'completed_frames': 0,
            'remaining_frames': 0,
            'success_rate': 0,
            'status': 'pending'
        }
    
    completed_frames = processed_frames + failed_frames
    progress_percent = (completed_frames / total_frames) * 100
    success_rate = (processed_frames / completed_frames * 100) if completed_frames > 0 else 0
    
    # Status bestimmen
    if completed_frames == 0:
        status = 'pending'
    elif completed_frames < total_frames:
        status = 'processing'
    elif failed_frames == total_frames:
        status = 'failed'
    else:
        status = 'completed'
    
    return {
        'progress_percent': round(progress_percent, 1),
        'completed_frames': completed_frames,
        'remaining_frames': total_frames - completed_frames,
        'success_rate': round(success_rate, 1),
        'status': status,
        'total_frames': total_frames,
        'processed_frames': processed_frames,
        'failed_frames': failed_frames
    }


def estimate_processing_time(total_frames: int, anonymization_level: str) -> Dict:
    """
    Schätzt die Verarbeitungszeit für die Anonymisierung.
    
    Args:
        total_frames: Anzahl der zu verarbeitenden Frames
        anonymization_level: Anonymisierungsgrad
    
    Returns:
        Dict mit Zeitschätzungen
    """
    # Geschätzte Verarbeitungszeit pro Frame (in Sekunden)
    time_per_frame = {
        'minimal': 0.5,
        'faces': 1.0,
        'full': 0.8
    }
    
    base_time = time_per_frame.get(anonymization_level, 1.0)
    estimated_seconds = total_frames * base_time
    
    # Overhead für Setup und Cleanup
    overhead_seconds = min(30, total_frames * 0.1)
    total_seconds = estimated_seconds + overhead_seconds
    
    return {
        'estimated_seconds': int(total_seconds),
        'estimated_minutes': round(total_seconds / 60, 1),
        'frames_per_second': round(1 / base_time, 1),
        'anonymization_level': anonymization_level
    }


def cleanup_expired_tokens():
    """
    Bereinigt abgelaufene Download-Tokens.
    
    Returns:
        Anzahl bereinigter Tokens
    """
    try:
        from endoreg_db.models import AnonymousFrame
        
        # Frames mit abgelaufenen Tokens finden
        expired_frames = AnonymousFrame.objects.filter(
            download_expires_at__lt=timezone.now(),
            download_token_hash__isnull=False
        )
        
        count = expired_frames.count()
        expired_frames.update(download_token_hash=None, download_expires_at=None)
        
        logger.info(f"Cleaned up {count} expired download tokens")
        return count
        
    except Exception as e:
        logger.error(f"Error cleaning up expired tokens: {str(e)}")
        return 0


def get_anonymization_statistics(video_id: Optional[int] = None) -> Dict:
    """
    Holt Statistiken zur Frame-Anonymisierung.
    
    Args:
        video_id: Optional - Video-ID für spezifische Statistiken
    
    Returns:
        Dict mit Statistiken
    """
    try:
        from endoreg_db.models import FrameAnonymizationRequest, AnonymousFrame
        from django.db.models import Count, Q
        
        # Basis-Queryset
        requests_qs = FrameAnonymizationRequest.objects.all()
        frames_qs = AnonymousFrame.objects.all()
        
        if video_id:
            requests_qs = requests_qs.filter(video_file_id=video_id)
            frames_qs = frames_qs.filter(video_file_id=video_id)
        
        # Request-Statistiken
        request_stats = requests_qs.aggregate(
            total_requests=Count('id'),
            pending_requests=Count('id', filter=Q(status='pending')),
            processing_requests=Count('id', filter=Q(status='processing')),
            completed_requests=Count('id', filter=Q(status='completed')),
            failed_requests=Count('id', filter=Q(status='failed'))
        )
        
        # Frame-Statistiken
        frame_stats = frames_qs.aggregate(
            total_frames=Count('id'),
            faces_anonymized=Count('id', filter=Q(anonymization_level='faces')),
            full_anonymized=Count('id', filter=Q(anonymization_level='full')),
            minimal_anonymized=Count('id', filter=Q(anonymization_level='minimal'))
        )
        
        # Disk-Space Statistiken
        total_size = 0
        for frame in frames_qs.values_list('anonymized_frame_path', flat=True):
            file_info = get_frame_file_info(frame)
            if file_info:
                total_size += file_info['size_bytes']
        
        return {
            'requests': request_stats,
            'frames': frame_stats,
            'storage': {
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2)
            },
            'video_id': video_id
        }
        
    except Exception as e:
        logger.error(f"Error getting anonymization statistics: {str(e)}")
        return {
            'requests': {},
            'frames': {},
            'storage': {},
            'error': str(e)
        }


def validate_frame_path(frame_path: str) -> bool:
    """
    Validiert einen Frame-Pfad auf Sicherheit.
    
    Args:
        frame_path: Zu validierender Pfad
    
    Returns:
        True wenn Pfad sicher ist
    """
    try:
        path = Path(frame_path).resolve()
        
        # Basis-Verzeichnisse definieren
        allowed_dirs = [
            Path(settings.MEDIA_ROOT).resolve(),
            Path(getattr(settings, 'FRAME_STORAGE_ROOT', '/tmp')).resolve()
        ]
        
        # Prüfen ob Pfad in erlaubten Verzeichnissen liegt
        for allowed_dir in allowed_dirs:
            try:
                path.relative_to(allowed_dir)
                return True
            except ValueError:
                continue
        
        logger.warning(f"Frame path outside allowed directories: {frame_path}")
        return False
        
    except Exception as e:
        logger.error(f"Error validating frame path {frame_path}: {str(e)}")
        return False


def get_video_frame_count(video_id: int) -> int:
    """
    Ermittelt die Anzahl verfügbarer Frames für ein Video.
    
    Args:
        video_id: Video-ID
    
    Returns:
        Anzahl der Frames
    """
    try:
        from endoreg_db.models import VideoFile
        
        video = VideoFile.objects.get(id=video_id)
        
        if not video.frame_dir:
            return 0
        
        frame_dir = Path(video.frame_dir)
        if not frame_dir.exists():
            return 0
        
        # Zähle alle Bilddateien
        frame_count = 0
        for ext in ['*.jpg', '*.jpeg', '*.png']:
            frame_count += len(list(frame_dir.glob(ext)))
        
        return frame_count
        
    except Exception as e:
        logger.error(f"Error counting frames for video {video_id}: {str(e)}")
        return 0


def format_file_size(size_bytes: int) -> str:
    """
    Formatiert Dateigröße in lesbares Format.
    
    Args:
        size_bytes: Größe in Bytes
    
    Returns:
        Formatierte Größe
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def log_anonymization_activity(activity_type: str, details: Dict, user_id: Optional[int] = None):
    """
    Protokolliert Anonymisierungsaktivitäten für Audit-Zwecke.
    
    Args:
        activity_type: Art der Aktivität
        details: Details der Aktivität
        user_id: Optional - Benutzer-ID
    """
    try:
        log_entry = {
            'timestamp': timezone.now().isoformat(),
            'activity_type': activity_type,
            'details': details,
            'user_id': user_id
        }
        
        logger.info(f"Anonymization activity: {activity_type}", extra=log_entry)
        
    except Exception as e:
        logger.error(f"Error logging anonymization activity: {str(e)}")