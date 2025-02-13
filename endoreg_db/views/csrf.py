from django.http import JsonResponse
from django.middleware.csrf import get_token

# New view to return the CSRF token in JSON format
def csrf_token_view(request):
    token = get_token(request)
    return JsonResponse({'csrf_token': token})