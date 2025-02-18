from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.exceptions import NotFound  
from rest_framework import status
from endoreg_db.models import Examination
from endoreg_db.serializers.examination import ExaminationSerializer

class ExaminationViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing examinations.
    """
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer

    def perform_create(self, serializer):
        """
        Handles the creation of an Examination, ensuring Many-to-Many relationships are processed correctly.
        """
        examination = serializer.save()  
        if 'examination_types' in self.request.data:
            examination.examination_types.set(self.request.data['examination_types'])  # Handle Many-to-Many

    def update(self, request, *args, **kwargs):
        """
        Handles updating an Examination, ensuring Many-to-Many relationships are updated properly.
        """
        partial = kwargs.pop('partial', False)  # Allows support for PATCH requests
        instance = self.get_object()  # Get the existing examination instance
        data = request.data.copy()  # Make a copy of request data

        # Handle Many-to-Many relationship separately
        examination_types = data.pop('examination_types', None)

        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated_instance = serializer.save()

        # If examination_types was provided, update the Many-to-Many relation
        if examination_types is not None:
            updated_instance.examination_types.set(examination_types) #.set() to properly assign related ExaminationTypes.

        return Response(serializer.data, status=status.HTTP_200_OK)

    
    """
        Soft delete: Instead of deleting an Examination, mark it as inactive.
        """
    """    def destroy(self, request, *args, **kwargs):
        
        instance = self.get_object()
        instance.is_deleted = True  # Assuming `is_deleted` field exists in your model
        instance.save()
        return Response({"message": "Examination archived, not deleted."}, status=status.HTTP_200_OK)"""

    def destroy(self, request, *args, **kwargs):
            """
            Permanently deletes an Examination from the database.
            """
            try:
                instance = self.get_object()  # Get the examination instance
                instance.delete()  # Permanently delete it
                return Response({"message": "Examination deleted successfully."}, status=status.HTTP_204_NO_CONTENT)
            except Examination.DoesNotExist:
                raise NotFound({"error": "Examination not found."})    