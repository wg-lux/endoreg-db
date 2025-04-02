#for keycloak need to make separate onne,demo
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from django.shortcuts import redirect, render
from django.conf import settings
from urllib.parse import urlencode
import requests

"""
    User hits /api/videos/
    Middleware checks for token; if missing, redirects to /login/
    /login/ redirects to Keycloak
    User logs in → Keycloak sends them back to /login/callback/
    /login/callback/ exchanges code for token, stores it in session
    User is redirected to /api/videos/ again
    Middleware now sees token, verifies it, injects user
    DRF view (VideoView) is allowed to execute and returns data
"""
class VideoView(APIView):
    permission_classes = [IsAuthenticated] #This uses DRF permissions to ensure request.user.is_authenticated == True.

    def get(self, request):
        """
        We already inject a mock user in the middleware, so this will pass if the middleware succeeded.
        Returns a message including the Keycloak username.
        """
        username = getattr(request.user, 'preferred_username', 'Unknown')
        return Response({"message": f"🎥 Hello, {username}. You are viewing protected videos!"})


def keycloak_login(request):

    """
    - This gets triggered when middleware redirects to /login/.
    """
    redirect_uri = request.build_absolute_uri('/login/callback/')
    print("Redirect URI:", redirect_uri)
    auth_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/auth"

    #OAuth2 Authorization Code Flow
    params = {
        "client_id": settings.KEYCLOAK_CLIENT_ID,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": redirect_uri,
    }
    # Redirect user to Keycloak login page.
    return redirect(f"{auth_url}?{urlencode(params)}")


import json
from django.http import HttpResponse

from django.http import HttpResponse, HttpResponseRedirect
import json

def keycloak_callback(request):

    #User lands here after login (Keycloak redirects here with code).
    code = request.GET.get("code")
    if not code:
        return HttpResponse(" No authorization code provided.", status=400)

    # Exchanges the code for an access_token.
    token_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
    redirect_uri = request.build_absolute_uri('/login/callback/')

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.KEYCLOAK_CLIENT_ID,
        "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
    }

    try:
        response = requests.post(token_url, data=data)

        print("Token Response Status:", response.status_code)
        print(" Token Response Body:", response.text)

        if response.status_code != 200:
            return HttpResponse(
                f"<h2> Token exchange failed</h2><pre>{response.text}</pre>",
                status=500
            )

        token_data = response.json()

        if "access_token" not in token_data:
            return HttpResponse(" Access token missing in response.", status=500)

        #  Stores the token in Django session. Middleware will use this on the next request.
        request.session["access_token"] = token_data["access_token"]
        request.session["refresh_token"] = token_data["refresh_token"]
        print("Refresh Token:", request.session.get("refresh_token"))

        return redirect("/api/videos/")

    except Exception as e:
        return HttpResponse(f" Exception during token exchange: {str(e)}", status=500)


def public_home(request):
    return HttpResponse("just for demo - This is a public home page — no login required.")
