"""
Dynamic permission utilities that adapt to environment settings.

This module provides permission classes that automatically adjust based on
the DEBUG setting and other environment configurations.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, Protocol, TypeAlias, cast

from django.conf import settings
from django.http.response import HttpResponseBase
from rest_framework.decorators import permission_classes as drf_permission_classes
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from lx_dtypes.models.contracts.permission_runtime import (
    DynamicPermissionConfigPayload,
    PermissionMode,
)

logger = logging.getLogger(__name__)

P = ParamSpec("P")
PermissionClass: TypeAlias = type[BasePermission]


class _AuthenticatedUser(Protocol):
    is_authenticated: bool


class _PermissionRequest(Protocol):
    user: _AuthenticatedUser


class DynamicAuthPermission:
    """
    Permission class that adapts based on environment settings.

    - In DEBUG mode: Allows access without authentication
    - In production (DEBUG=False): Requires authentication
    """

    @staticmethod
    def get_permission_classes() -> list[PermissionClass]:
        """
        Returns appropriate permission classes based on current settings.
        """
        if getattr(settings, "DEBUG", False):
            logger.info("DEBUG mode detected - allowing unauthenticated access")
            return [AllowAny]
        logger.info("Production mode detected - requiring authentication")
        return [IsAuthenticated]


def dynamic_permission_classes(
    force_auth: PermissionMode = "default",
) -> Callable[[Callable[P, HttpResponseBase]], Callable[P, HttpResponseBase]]:
    """
    Decorator that applies permission classes based on environment settings.
    """
    config = DynamicPermissionConfigPayload.model_validate({"mode": force_auth})

    def decorator(
        view_func: Callable[P, HttpResponseBase],
    ) -> Callable[P, HttpResponseBase]:
        @wraps(view_func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> HttpResponseBase:
            return view_func(*args, **kwargs)

        if config.mode == "force_auth":
            permission_cls = [IsAuthenticated]
            logger.info("View %s - forced authentication required", view_func.__name__)
        elif config.mode == "force_public":
            permission_cls = [AllowAny]
            logger.info("View %s - forced public access", view_func.__name__)
        else:
            permission_cls = DynamicAuthPermission.get_permission_classes()
            logger.info(
                "View %s - dynamic permissions: %s",
                view_func.__name__,
                permission_cls,
            )

        return cast(
            Callable[P, HttpResponseBase],
            drf_permission_classes(permission_cls)(wrapper),
        )

    return decorator


def get_auth_required() -> bool:
    """
    Simple function to check if authentication is required in current environment.
    """
    return not getattr(settings, "DEBUG", False)


def is_debug_mode() -> bool:
    """
    Robustly determine if debug mode is enabled, checking both Django settings
    and environment variable. Also treats active pytest sessions as debug to
    simplify API tests.
    """
    truthy = {"1", "true", "yes", "on"}
    env_debug = str(os.environ.get("DJANGO_DEBUG", "false")).lower() in truthy
    settings_debug = bool(getattr(settings, "DEBUG", False))
    pytest_active = "PYTEST_CURRENT_TEST" in os.environ
    result = settings_debug or env_debug or pytest_active
    logger.info(
        "is_debug_mode: env=%s, settings=%s, pytest=%s, result=%s",
        env_debug,
        settings_debug,
        pytest_active,
        result,
    )
    return result


def get_debug_permissions() -> list[PermissionClass]:
    return [AllowAny] if is_debug_mode() else [IsAuthenticated]


DEBUG_PERMISSIONS = get_debug_permissions()
ALWAYS_AUTH_PERMISSIONS = [IsAuthenticated]
ALWAYS_PUBLIC_PERMISSIONS = [AllowAny]

if is_debug_mode():
    logger.info("Authentication disabled for DEBUG mode (robust check)")
else:
    logger.info("Authentication required for production mode (robust check)")


class EnvironmentAwarePermission(BasePermission):
    """
    Custom permission class that can be used directly in DRF views.
    Honors both Django settings.DEBUG and DJANGO_DEBUG env var.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        if is_debug_mode():
            logger.debug("DEBUG mode - granting access to %s", view.__class__.__name__)
            return True

        request_user = cast(_PermissionRequest, request).user
        is_authenticated = bool(getattr(request_user, "is_authenticated", False))
        logger.debug(
            "Production mode - authentication check for %s: %s",
            view.__class__.__name__,
            is_authenticated,
        )
        return is_authenticated

    def has_object_permission(
        self, request: Request, view: APIView, obj: object
    ) -> bool:
        return self.has_permission(request, view)
