from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from endoreg_db.models import LabelVideoSegment, VideoFile
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS


class LabelVideoSegmentValidateView(APIView):
    """
    POST /api/label-video-segment/<int:segment_id>/validate/
    
    Validiert ein einzelnes LabelVideoSegment und markiert es als verifiziert.
    Dies wird verwendet, um vom Benutzer überprüfte Segment-Annotationen zu bestätigen.
    
    Body (optional):
    {
      "is_validated": true,  // optional, default true
      "notes": "..."         // optional, Validierungsnotizen
    }
    """
    permission_classes = DEBUG_PERMISSIONS

    @transaction.atomic
    def post(self, request, segment_id: int):
        try:
            # Segment abrufen
            segment = LabelVideoSegment.objects.select_related('state', 'video_file', 'label').get(pk=segment_id)
            
            # Validierungsstatus aus Request (default: True)
            is_validated = request.data.get('is_validated', True)
            notes = request.data.get('notes', '')
            
            # State-Objekt abrufen oder erstellen
            if not hasattr(segment, 'state') or segment.state is None:
                # State muss existieren - wenn nicht, könnte ein Model-Problem vorliegen
                return Response({
                    "error": "Segment has no state object. Cannot validate."
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # State aktualisieren
            segment.state.is_validated = is_validated
            if notes:
                # Falls ein notes-Feld existiert
                if hasattr(segment.state, 'validation_notes'):
                    segment.state.validation_notes = notes
            segment.state.save()
            
            return Response({
                "message": f"Segment {segment_id} validation status updated.",
                "segment_id": segment_id,
                "is_validated": is_validated,
                "label": segment.label.name if segment.label else None,
                "video_id": segment.video_file.id if segment.video_file else None,
                "start_frame": segment.start_frame_number,
                "end_frame": segment.end_frame_number
            }, status=status.HTTP_200_OK)
            
        except LabelVideoSegment.DoesNotExist:
            return Response({
                "error": f"Segment {segment_id} not found."
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error validating segment {segment_id}: {e}")
            return Response({
                "error": f"Validation failed: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BulkSegmentValidateView(APIView):
    """
    POST /api/label-video-segments/validate-bulk/
    
    Validiert mehrere LabelVideoSegments gleichzeitig.
    Nützlich für Batch-Validierung nach Review.
    
    Body:
    {
      "segment_ids": [1, 2, 3, ...],
      "is_validated": true,  // optional, default true
      "notes": "..."         // optional, gilt für alle Segmente
    }
    """
    permission_classes = DEBUG_PERMISSIONS

    @transaction.atomic
    def post(self, request):
        segment_ids = request.data.get('segment_ids', [])
        is_validated = request.data.get('is_validated', True)
        notes = request.data.get('notes', '')
        
        if not segment_ids:
            return Response({
                "error": "segment_ids is required"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Alle Segmente abrufen
            segments = LabelVideoSegment.objects.filter(pk__in=segment_ids).select_related('state')
            
            if not segments.exists():
                return Response({
                    "error": "No segments found with provided IDs"
                }, status=status.HTTP_404_NOT_FOUND)
            
            updated_count = 0
            failed_ids = []
            
            for segment in segments:
                try:
                    if segment.state:
                        segment.state.is_validated = is_validated
                        if notes and hasattr(segment.state, 'validation_notes'):
                            segment.state.validation_notes = notes
                        segment.state.save()
                        updated_count += 1
                    else:
                        failed_ids.append(segment.id)
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Error validating segment {segment.id}: {e}")
                    failed_ids.append(segment.id)
            
            response_data = {
                "message": f"Bulk validation completed. {updated_count} segments updated.",
                "updated_count": updated_count,
                "requested_count": len(segment_ids),
                "is_validated": is_validated
            }
            
            if failed_ids:
                response_data["failed_ids"] = failed_ids
                response_data["warning"] = f"{len(failed_ids)} segments could not be validated"
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in bulk validation: {e}")
            return Response({
                "error": f"Bulk validation failed: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VideoSegmentValidationCompleteView(APIView):
    """
    POST /api/videos/<int:video_id>/segments/validate-complete/
    
    Markiert alle Segmente eines Videos als validiert.
    Nützlich nach vollständiger Review eines Videos.
    
    Body (optional):
    {
      "label_name": "...",   // optional, nur Segmente mit diesem Label validieren
      "notes": "..."         // optional
    }
    """
    permission_classes = DEBUG_PERMISSIONS

    @transaction.atomic
    def post(self, request, video_id: int):
        try:
            # Video abrufen
            video = VideoFile.objects.get(pk=video_id)
            
            label_name = request.data.get('label_name')
            notes = request.data.get('notes', '')
            
            # Segmente filtern
            segments_query = LabelVideoSegment.objects.filter(video_file=video).select_related('state', 'label')
            
            if label_name:
                segments_query = segments_query.filter(label__name=label_name)
            
            segments = segments_query.all()
            
            if not segments.exists():
                return Response({
                    "message": "No segments found to validate",
                    "video_id": video_id,
                    "updated_count": 0
                }, status=status.HTTP_200_OK)
            
            updated_count = 0
            failed_count = 0
            
            for segment in segments:
                try:
                    if segment.state:
                        segment.state.is_validated = True
                        if notes and hasattr(segment.state, 'validation_notes'):
                            segment.state.validation_notes = notes
                        segment.state.save()
                        updated_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Error validating segment {segment.id}: {e}")
                    failed_count += 1
            
            return Response({
                "message": f"Video segment validation completed for video {video_id}",
                "video_id": video_id,
                "total_segments": len(segments),
                "updated_count": updated_count,
                "failed_count": failed_count,
                "label_filter": label_name
            }, status=status.HTTP_200_OK)
            
        except VideoFile.DoesNotExist:
            return Response({
                "error": f"Video {video_id} not found"
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error completing validation for video {video_id}: {e}")
            return Response({
                "error": f"Validation completion failed: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
