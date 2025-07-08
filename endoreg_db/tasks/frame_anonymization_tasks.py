"""
Celery Tasks for Frame Anonymization

This file contains asynchronous tasks for anonymizing video frames.
The tasks are executed via Celery and can process time-consuming anonymization
operations in the background.
"""

import os
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Optional
from PIL import Image, ImageDraw
import cv2
import numpy as np

from celery_app import shared_task
from django.conf import settings
from django.utils import timezone

from endoreg_db.models import (
    VideoFile,
    LabelVideoSegment,
    FrameAnonymizationRequest,
    AnonymousFrame
)

logger = logging.getLogger(__name__)

# Konfiguration für Anonymisierung
ANONYMIZATION_CONFIG = {
    'faces': {
        'blur_strength': 15,
        'detection_confidence': 0.7
    },
    'full': {
        'blur_strength': 25,
        'detection_confidence': 0.5
    },
    'minimal': {
        'blur_strength': 8,
        'detection_confidence': 0.8
    }
}

@shared_task(bind=True, max_retries=3)
def process_frame_anonymization(self, request_id: str, video_id: int, segment_ids: List[int], 
                               anonymization_level: str = 'faces', output_format: str = 'jpg'):
    """
    Asynchroner Task zur Anonymisierung von Video-Frames.
    
    Args:
        request_id: Eindeutige ID der Anonymisierungsanfrage
        video_id: ID des Videos
        segment_ids: Liste der zu verarbeitenden Segment-IDs
        anonymization_level: Anonymisierungsgrad ('faces', 'full', 'minimal')
        output_format: Ausgabeformat ('jpg', 'png')
    
    Returns:
        Dict mit Verarbeitungsstatistiken
    """
    try:
        # Status auf "processing" setzen
        request_obj = FrameAnonymizationRequest.objects.get(request_id=request_id)
        request_obj.status = 'processing'
        request_obj.started_at = timezone.now()
        request_obj.save()
        
        logger.info(f"Starting frame anonymization for request {request_id}")
        
        # Video und Segmente laden
        video = VideoFile.objects.get(id=video_id)
        segments = LabelVideoSegment.objects.filter(
            id__in=segment_ids,
            video_file=video
        )
        
        if not segments.exists():
            raise ValueError(f"No valid segments found for video {video_id}")
        
        # Statistiken initialisieren
        stats = {
            'total_frames': 0,
            'processed_frames': 0,
            'failed_frames': 0,
            'segments_processed': 0
        }
        
        # Jedes Segment verarbeiten
        for segment in segments:
            try:
                segment_stats = _process_segment_frames(
                    request_obj, video, segment, anonymization_level, output_format
                )
                
                stats['total_frames'] += segment_stats['total_frames']
                stats['processed_frames'] += segment_stats['processed_frames']
                stats['failed_frames'] += segment_stats['failed_frames']
                stats['segments_processed'] += 1
                
                # Progress Update
                progress = (stats['segments_processed'] / len(segments)) * 100
                self.update_state(
                    state='PROGRESS',
                    meta={'progress': progress, 'current_segment': segment.id}
                )
                
            except Exception as e:
                logger.error(f"Failed to process segment {segment.id}: {str(e)}")
                stats['failed_frames'] += 1
                continue
        
        # Request als completed markieren
        request_obj.status = 'completed'
        request_obj.completed_at = timezone.now()
        request_obj.result_data = stats
        request_obj.save()
        
        logger.info(f"Completed frame anonymization for request {request_id}: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"Frame anonymization failed for request {request_id}: {str(e)}")
        
        # Request als failed markieren
        try:
            request_obj = FrameAnonymizationRequest.objects.get(request_id=request_id)
            request_obj.status = 'failed'
            request_obj.error_message = str(e)
            request_obj.completed_at = timezone.now()
            request_obj.save()
        except Exception:
            pass
        
        # Retry logic
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying frame anonymization for request {request_id} (attempt {self.request.retries + 1})")
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        
        raise


def _process_segment_frames(request_obj: FrameAnonymizationRequest, video: VideoFile, 
                           segment: LabelVideoSegment, anonymization_level: str, 
                           output_format: str) -> Dict:
    """
    Verarbeitet alle Frames eines Video-Segments.
    
    Args:
        request_obj: Anonymisierungsanfrage-Objekt
        video: Video-Objekt
        segment: Video-Segment-Objekt
        anonymization_level: Anonymisierungsgrad
        output_format: Ausgabeformat
    
    Returns:
        Dict mit Segment-Statistiken
    """
    stats = {'total_frames': 0, 'processed_frames': 0, 'failed_frames': 0}
    
    try:
        # Frame-Pfade für das Segment ermitteln
        frame_paths = _get_segment_frame_paths(video, segment)
        stats['total_frames'] = len(frame_paths)
        
        if not frame_paths:
            logger.warning(f"No frames found for segment {segment.id}")
            return stats
        
        # Anonymisierungsalgorithmus laden
        anonymizer = _get_anonymization_algorithm(anonymization_level)
        
        # Jeden Frame verarbeiten
        for frame_path in frame_paths:
            try:
                anonymized_path = _anonymize_single_frame(
                    frame_path, anonymizer, anonymization_level, output_format
                )
                
                if anonymized_path:
                    # AnonymousFrame-Eintrag erstellen
                    AnonymousFrame.objects.create(
                        anonymization_request=request_obj,
                        video_file=video,
                        segment=segment,
                        original_frame_path=str(frame_path),
                        anonymized_frame_path=anonymized_path,
                        frame_number=_extract_frame_number(frame_path),
                        anonymization_level=anonymization_level,
                        output_format=output_format
                    )
                    stats['processed_frames'] += 1
                else:
                    stats['failed_frames'] += 1
                    
            except Exception as e:
                logger.error(f"Failed to process frame {frame_path}: {str(e)}")
                stats['failed_frames'] += 1
                continue
                
    except Exception as e:
        logger.error(f"Failed to process segment {segment.id}: {str(e)}")
        stats['failed_frames'] = stats['total_frames']
    
    return stats


def _get_segment_frame_paths(video: VideoFile, segment: LabelVideoSegment) -> List[Path]:
    """
    Ermittelt alle Frame-Pfade für ein Video-Segment.
    
    Args:
        video: Video-Objekt
        segment: Video-Segment
    
    Returns:
        Liste der Frame-Pfade
    """
    frame_paths = []
    
    try:
        if not video.frame_dir:
            logger.warning(f"No frame directory for video {video.id}")
            return frame_paths
        
        frame_dir = Path(video.frame_dir)
        if not frame_dir.exists():
            logger.warning(f"Frame directory does not exist: {frame_dir}")
            return frame_paths
        
        # Frame-Bereich basierend auf Segment-Grenzen
        start_frame = segment.start_frame_number
        end_frame = segment.end_frame_number
        
        # Alle Frames im Bereich sammeln
        for frame_num in range(start_frame, end_frame + 1):
            # Verschiedene Frame-Namenskonventionen unterstützen
            possible_names = [
                f"frame_{frame_num:06d}.jpg",
                f"frame_{frame_num:06d}.png",
                f"{frame_num:06d}.jpg",
                f"{frame_num:06d}.png",
                f"frame_{frame_num}.jpg",
                f"frame_{frame_num}.png"
            ]
            
            for name in possible_names:
                frame_path = frame_dir / name
                if frame_path.exists():
                    frame_paths.append(frame_path)
                    break
    
    except Exception as e:
        logger.error(f"Error getting frame paths for segment {segment.id}: {str(e)}")
    
    return frame_paths


def _get_anonymization_algorithm(anonymization_level: str):
    """
    Lädt den entsprechenden Anonymisierungsalgorithmus.
    
    Args:
        anonymization_level: Anonymisierungsgrad
    
    Returns:
        Anonymisierungsalgorithmus oder None
    """
    try:
        # Hier würde normalerweise ein ML-Modell geladen werden
        # Für Demo-Zwecke verwenden wir einfache OpenCV-Gesichtserkennung
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        return face_cascade
    except Exception as e:
        logger.error(f"Failed to load anonymization algorithm: {str(e)}")
        return None


def _anonymize_single_frame(frame_path: Path, anonymizer, anonymization_level: str, 
                           output_format: str) -> Optional[str]:
    """
    Anonymisiert einen einzelnen Frame.
    
    Args:
        frame_path: Pfad zum Original-Frame
        anonymizer: Anonymisierungsalgorithmus
        anonymization_level: Anonymisierungsgrad
        output_format: Ausgabeformat
    
    Returns:
        Pfad zum anonymisierten Frame oder None bei Fehler
    """
    try:
        # Original-Frame laden
        image = cv2.imread(str(frame_path))
        if image is None:
            logger.error(f"Could not load image: {frame_path}")
            return None
        
        # Anonymisierung anwenden
        anonymized_image = _apply_anonymization(image, anonymizer, anonymization_level)
        
        # Ausgabepfad generieren
        output_dir = _get_anonymized_frames_dir()
        output_filename = f"anon_{frame_path.stem}_{uuid.uuid4().hex[:8]}.{output_format}"
        output_path = output_dir / output_filename
        
        # Anonymisierten Frame speichern
        success = cv2.imwrite(str(output_path), anonymized_image)
        
        if success:
            return str(output_path)
        else:
            logger.error(f"Failed to save anonymized frame: {output_path}")
            return None
            
    except Exception as e:
        logger.error(f"Error anonymizing frame {frame_path}: {str(e)}")
        return None


def _apply_anonymization(image: np.ndarray, anonymizer, anonymization_level: str) -> np.ndarray:
    """
    Wendet Anonymisierung auf ein Bild an.
    
    Args:
        image: Original-Bild
        anonymizer: Anonymisierungsalgorithmus
        anonymization_level: Anonymisierungsgrad
    
    Returns:
        Anonymisiertes Bild
    """
    try:
        config = ANONYMIZATION_CONFIG.get(anonymization_level, ANONYMIZATION_CONFIG['faces'])
        
        if anonymizer is None:
            # Fallback: Einfache Unschärfe auf das gesamte Bild
            blur_strength = config['blur_strength']
            return cv2.GaussianBlur(image, (blur_strength, blur_strength), 0)
        
        # Gesichtserkennung
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = anonymizer.detectMultiScale(
            gray, 
            scaleFactor=1.1, 
            minNeighbors=5, 
            minSize=(30, 30)
        )
        
        # Anonymisierung auf erkannte Gesichter anwenden
        for (x, y, w, h) in faces:
            if anonymization_level == 'faces':
                # Nur Gesichter unscharf machen
                face_region = image[y:y+h, x:x+w]
                blurred_face = cv2.GaussianBlur(face_region, (config['blur_strength'], config['blur_strength']), 0)
                image[y:y+h, x:x+w] = blurred_face
            elif anonymization_level == 'full':
                # Schwarzer Balken über Gesichter
                cv2.rectangle(image, (x, y), (x+w, y+h), (0, 0, 0), -1)
            elif anonymization_level == 'minimal':
                # Leichte Unschärfe nur auf Gesichter
                face_region = image[y:y+h, x:x+w]
                blurred_face = cv2.GaussianBlur(face_region, (config['blur_strength'], config['blur_strength']), 0)
                image[y:y+h, x:x+w] = blurred_face
        
        return image
        
    except Exception as e:
        logger.error(f"Error applying anonymization: {str(e)}")
        # Fallback: Original-Bild zurückgeben
        return image


def _get_anonymized_frames_dir() -> Path:
    """
    Erstellt und gibt das Verzeichnis für anonymisierte Frames zurück.
    
    Returns:
        Pfad zum Anonymisierungs-Verzeichnis
    """
    base_dir = Path(getattr(settings, 'MEDIA_ROOT', '/tmp'))
    anon_dir = base_dir / 'anonymized_frames'
    anon_dir.mkdir(parents=True, exist_ok=True)
    return anon_dir


def _extract_frame_number(frame_path: Path) -> int:
    """
    Extrahiert die Frame-Nummer aus dem Dateinamen.
    
    Args:
        frame_path: Pfad zum Frame
    
    Returns:
        Frame-Nummer oder 0 bei Fehler
    """
    try:
        # Verschiedene Namenskonventionen unterstützen
        stem = frame_path.stem
        
        if stem.startswith('frame_'):
            return int(stem.split('_')[1])
        elif stem.isdigit():
            return int(stem)
        else:
            # Versuche Zahlen aus dem Dateinamen zu extrahieren
            import re
            numbers = re.findall(r'\d+', stem)
            if numbers:
                return int(numbers[-1])  # Letzte gefundene Zahl verwenden
    
    except (ValueError, IndexError):
        pass
    
    return 0


@shared_task
def cleanup_old_anonymized_frames(days_old: int = 7):
    """
    Bereinigt alte anonymisierte Frames.
    
    Args:
        days_old: Alter in Tagen, ab dem Frames gelöscht werden
    """
    try:
        from datetime import timedelta
        
        cutoff_date = timezone.now() - timedelta(days=days_old)
        
        # Alte AnonymousFrame-Einträge finden
        old_frames = AnonymousFrame.objects.filter(created_at__lt=cutoff_date)
        
        deleted_files = 0
        deleted_records = 0
        
        for frame in old_frames:
            try:
                # Datei löschen
                if frame.anonymized_frame_path and Path(frame.anonymized_frame_path).exists():
                    Path(frame.anonymized_frame_path).unlink()
                    deleted_files += 1
                
                # DB-Eintrag löschen
                frame.delete()
                deleted_records += 1
                
            except Exception as e:
                logger.error(f"Failed to delete frame {frame.id}: {str(e)}")
        
        logger.info(f"Cleanup completed: {deleted_files} files and {deleted_records} records deleted")
        
        return {
            'deleted_files': deleted_files,
            'deleted_records': deleted_records
        }
        
    except Exception as e:
        logger.error(f"Cleanup task failed: {str(e)}")
        raise