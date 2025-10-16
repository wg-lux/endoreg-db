from endoreg_db.utils.permissions import EnvironmentAwarePermission


from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from celery import current_app

class TaskStatusView(APIView):
    """
    GET /api/task-status/{task_id}/ - Get status of async task
    """
    #TODO refactor, this should be specified as video task status or moved to a more appropriate module
    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, task_id):
        try:
            task_result = current_app.AsyncResult(task_id)

            response_data = {
                'task_id': task_id,
                'status': task_result.status,
                'progress': 0,
                'message': 'Task pending...'
            }

            if task_result.status == 'PENDING':
                response_data['message'] = 'Task is waiting to be processed'
            elif task_result.status == 'PROGRESS':
                response_data.update(task_result.result or {})
            elif task_result.status == 'SUCCESS':
                response_data.update({
                    'progress': 100,
                    'message': 'Task completed successfully',
                    'result': task_result.result
                })
            elif task_result.status == 'FAILURE':
                response_data.update({
                    'message': str(task_result.result),
                    'error': True
                })

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Failed to get task status: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )