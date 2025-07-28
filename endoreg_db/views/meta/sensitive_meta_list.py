from endoreg_db.models import SensitiveMeta
from endoreg_db.serializers.meta.sensitive_meta_detail import SensitiveMetaDetailSerializer

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
import logging

logger = logging.getLogger(__name__)


class SensitiveMetaListView(APIView):
    """
    API endpoint to list SensitiveMeta entries with filtering options.

    GET: Returns paginated list of SensitiveMeta entries
    """

    permission_classes = DEBUG_PERMISSIONS  

    def get(self, request):
        """
        List SensitiveMeta entries with optional filtering.

        Query Parameters:
        - verified: Filter by verification status (true/false)
        - center: Filter by center name
        - limit: Number of results to return (default 20)
        - offset: Offset for pagination (default 0)
        """
        try:
            # Base queryset with related data
            queryset = SensitiveMeta.objects.select_related(
                'center',
                'patient_gender',
                'state'
            ).prefetch_related('examiners')

            # Apply filters
            verified_filter = request.query_params.get('verified')
            if verified_filter is not None:
                if verified_filter.lower() == 'true':
                    # Filter for entries where both DOB and names are verified
                    queryset = queryset.filter(
                        state__dob_verified=True,
                        state__names_verified=True
                    )
                elif verified_filter.lower() == 'false':
                    # Filter for entries that are not fully verified
                    queryset = queryset.exclude(
                        state__dob_verified=True,
                        state__names_verified=True
                    )

            center_filter = request.query_params.get('center')
            if center_filter:
                queryset = queryset.filter(center__name__icontains=center_filter)

            # Pagination
            try:
                limit = int(request.query_params.get('limit', 10))
                if limit < 0:
                    limit = 10
            except (ValueError, TypeError):
                limit = 10
            
            try:
                offset = int(request.query_params.get('offset', 0))
                if offset < 0:
                    offset = 0
            except (ValueError, TypeError):
                offset = 0

            # Enforce a maximum limit
            if limit > 100:
                limit = 100

            paginated_queryset = queryset[offset:offset + limit]

            # Serialize results
            serializer = SensitiveMetaDetailSerializer(paginated_queryset, many=True)

            response_data = {
                "results": serializer.data,
                "total_count": queryset.count(),
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < queryset.count()
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {"error": f"Invalid query parameters: {e}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Error listing SensitiveMeta entries: {e}")
            return Response(
                {"error": "Internal server error occurred"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )