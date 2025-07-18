"""
Dynamic permission utilities that adapt to environment settings.

This module provides permission classes that automatically adjust based on
the DEBUG setting and other environment configurations.
"""

from django.conf import settings
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.decorators import permission_classes as drf_permission_classes
from functools import wraps
import logging

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
    
    Usage:
        @dynamic_permission_classes()
        def my_view(request):
            pass
            
        @dynamic_permission_classes(force_auth=True) 
        def sensitive_view(request):
            pass
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


class EnvironmentAwarePermission:
    """
    Custom permission class that can be used directly in DRF views.
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
        if getattr(settings, 'DEBUG', False):
            # In DEBUG mode, always allow access
            logger.debug(f"DEBUG mode - granting access to {view.__class__.__name__}")
            return True
        else:
            # In production, require authentication
            is_authenticated = request.user and request.user.is_authenticated
            logger.debug(f"Production mode - authentication check for {view.__class__.__name__}: {is_authenticated}")
            return is_authenticated
    
    def has_object_permission(self, request, view, obj):
        """
        Object-level permission check.
        """
        return self.has_permission(request, view)


ALWAYS_AUTH_PERMISSIONS = [IsAuthenticated]
ALWAYS_PUBLIC_PERMISSIONS = [AllowAny]

# Log the current permission mode
if getattr(settings, 'DEBUG', False):
    logger.info("🔓 Authentication disabled for DEBUG mode")
else:
    logger.info("🔒 Authentication required for production mode")