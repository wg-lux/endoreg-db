#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
import subprocess
import signal
import atexit
from pathlib import Path

DJANGO_SETTINGS_MODULE = os.environ.get("DJANGO_SETTINGS_MODULE")

# Global variable to track the token validator process
token_validator_process = None

def start_token_validator_service():
    """Start the token validator service as a subprocess."""
    global token_validator_process
    
    # Get the project root directory (parent of endoreg-db)
    project_root = Path(__file__).parent.parent
    token_validator_script = project_root / "start-token-validator.sh"
    
    if not token_validator_script.exists():
        print(f"⚠️  Token validator script not found at: {token_validator_script}")
        return
    
    try:
        print("🔐 Starting Keycloak Token Validator Service...")
        
        # Set environment variables for the token validator
        env = os.environ.copy()
        env.update({
            'KEYCLOAK_REALM': env.get('KEYCLOAK_REALM', 'master'),
            'KEYCLOAK_SERVER_URL': env.get('KEYCLOAK_SERVER_URL', 'https://keycloak.endo-reg.net'),
            'KEYCLOAK_CLIENT_ID': env.get('KEYCLOAK_CLIENT_ID', 'lx-frontend'),
            'KEYCLOAK_CLIENT_SECRET': env.get('KEYCLOAK_CLIENT_SECRET',),
            'TOKEN_VALIDATOR_PORT': env.get('TOKEN_VALIDATOR_PORT', '3001'),
        })
        
        # Start the token validator using bash (since it's a shell script)
        token_validator_process = subprocess.Popen(
            ['bash', str(token_validator_script)],
            cwd=str(project_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid  # Create new process group for clean shutdown
        )
        
        print(f"✅ Token Validator Service started with PID: {token_validator_process.pid}")
        
    except Exception as e:
        print(f"🚨 Failed to start Token Validator Service: {e}")

def stop_token_validator_service():
    """Stop the token validator service."""
    global token_validator_process
    
    if token_validator_process and token_validator_process.poll() is None:
        try:
            print("🛑 Stopping Token Validator Service...")
            # Send SIGTERM to the process group
            os.killpg(os.getpgid(token_validator_process.pid), signal.SIGTERM)
            
            # Wait for process to terminate
            token_validator_process.wait(timeout=5)
            print("✅ Token Validator Service stopped")
            
        except subprocess.TimeoutExpired:
            # Force kill if it doesn't stop gracefully
            print("⚠️  Force killing Token Validator Service...")
            os.killpg(os.getpgid(token_validator_process.pid), signal.SIGKILL)
            token_validator_process.wait()
            
        except Exception as e:
            print(f"🚨 Error stopping Token Validator Service: {e}")
        
        finally:
            token_validator_process = None

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    print(f"\n🔄 Received signal {signum}, shutting down...")
    stop_token_validator_service()
    sys.exit(0)

def main():
    """Run Django administrative tasks.
    
    Imports and executes Django's command-line management utility using system arguments.
    Raises an ImportError if Django is not installed or the virtual environment is not activated.
    """

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    
    # Only start token validator for runserver command
    if len(sys.argv) >= 2 and sys.argv[1] == 'runserver':
        print("🔧 Django runserver detected - starting Token Validator Service...")
        
        # Register cleanup handlers
        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler)  # Termination
        atexit.register(stop_token_validator_service)  # Process exit
        
        # Start the token validator service
        start_token_validator_service()
        
        # Give the service a moment to start
        import time
        time.sleep(2)
        
        print("🚀 Starting Django development server...")
    
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
