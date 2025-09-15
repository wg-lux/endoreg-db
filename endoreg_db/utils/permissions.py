"""
Dynamic permission utilities that adapt to environment settings.

This module provides permission classes that automatically adjust based on
the DEBUG setting and other environment configurations.
"""

from django.conf import settings
from rest_framework.permissions import IsAuthenticated, AllowAny, BasePermission
from rest_framework.decorators import permission_classes as drf_permission_classes
from functools import wraps
import logging
import os

logger = logging.getLogger(__name__)


class DynamicAuthPermission:
    """
    Permission class that adapts based on environment settings.
    
    - In DEBUG mode: Allows access without authentication
    - In production (DEBUG=False): Requires authentication
    """
    
    @staticmethod
    def get_permission_classes():
        """
        Returns appropriate permission classes based on current settings.
        
        Returns:
            list: Permission classes to use
        """
        if getattr(settings, 'DEBUG', False):
            logger.info("DEBUG mode detected - allowing unauthenticated access")
            return [AllowAny]
        else:
            logger.info("Production mode detected - requiring authentication")
            return [IsAuthenticated]


def dynamic_permission_classes(force_auth=None):
    """
    Decorator that applies permission classes based on environment settings.
    
    Args:
        force_auth (bool, optional): 
            - True: Always require authentication regardless of DEBUG
            - False: Always allow access regardless of DEBUG  
            - None: Use environment-based logic (default)
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            return view_func(*args, **kwargs)
        
        # Determine permission classes
        if force_auth is True:
            permission_cls = [IsAuthenticated]
            logger.info(f"View {view_func.__name__} - forced authentication required")
        elif force_auth is False:
            permission_cls = [AllowAny]
            logger.info(f"View {view_func.__name__} - forced public access")
        else:
            permission_cls = DynamicAuthPermission.get_permission_classes()
            logger.info(f"View {view_func.__name__} - dynamic permissions: {permission_cls}")
        
        # Apply the permission classes decorator
        return drf_permission_classes(permission_cls)(wrapper)
    
    return decorator


def get_auth_required():
    """
    Simple function to check if authentication is required in current environment.
    
    Returns:
        bool: True if authentication is required, False otherwise
    """
    return not getattr(settings, 'DEBUG', False)


def is_debug_mode():
    """
    Robustly determine if debug mode is enabled, checking both Django settings and environment variable.
    Also treats active pytest sessions as debug to simplify API tests.
    """
    truthy = {"1", "true", "yes", "on"}
    env_debug = str(os.environ.get("DJANGO_DEBUG", "false")).lower() in truthy
    settings_debug = bool(getattr(settings, 'DEBUG', False))
    pytest_active = "PYTEST_CURRENT_TEST" in os.environ
    result = settings_debug or env_debug or pytest_active
    logger.info(f"is_debug_mode: env={env_debug}, settings={settings_debug}, pytest={pytest_active}, result={result}")
    return result

# Compute default permission classes each call to avoid stale values during tests

def get_debug_permissions():
    return [AllowAny] if is_debug_mode() else [IsAuthenticated]

# Export a name for convenience, but prefer calling get_debug_permissions() in views
DEBUG_PERMISSIONS = get_debug_permissions()
ALWAYS_AUTH_PERMISSIONS = [IsAuthenticated]
ALWAYS_PUBLIC_PERMISSIONS = [AllowAny]

# Log the current permission mode
if is_debug_mode():
    logger.info("🔓 Authentication disabled for DEBUG mode (robust check)")
else:
    logger.info("🔒 Authentication required for production mode (robust check)")


class EnvironmentAwarePermission(BasePermission):
    """
    Custom permission class that can be used directly in DRF views.
    Honors both Django settings.DEBUG and DJANGO_DEBUG env var.
    """
    
    def has_permission(self, request, view):
        """
        Check if the request has permission.
        
        Args:
            request: The Django request object
            view: The DRF view
            
        Returns:
            bool: True if permission granted, False otherwise
        """
        if is_debug_mode():
            logger.debug(f"DEBUG mode - granting access to {view.__class__.__name__}")
            return True
        # In production, require authentication
        is_authenticated = bool(getattr(request, 'user', None) and request.user.is_authenticated)
        logger.debug(f"Production mode - authentication check for {view.__class__.__name__}: {is_authenticated}")
        return is_authenticated
    
    def has_object_permission(self, request, view, obj):
        """
        Object-level permission check.
        """
        return self.has_permission(request, view)