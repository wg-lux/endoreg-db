"""
Debug script to trace exactly what happens during optimized video test.
"""
import os
import sys
import django
from pathlib import Path

# Add the project root to Python path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings_test')
os.environ.setdefault('RUN_VIDEO_TESTS', 'true')
os.environ.setdefault('SKIP_EXPENSIVE_TESTS', 'false')

# Setup Django
django.setup()

from tests.helpers.default_objects import get_default_video_file
from tests.media.video.helper import get_random_video_path_by_examination_alias
from django.conf import settings

def trace_video_creation():
    """Trace the video creation process to find where files get deleted."""
    
    print("=== TRACING VIDEO CREATION PROCESS ===")
    
    asset_dir = settings.ASSET_DIR
    print(f"ASSET_DIR: {asset_dir}")
    
    # Check initial state
    initial_files = list(asset_dir.glob("*.mp4"))
    print(f"Initial video assets: {[f.name for f in initial_files]}")
    
    # Hook into file operations to trace deletions
    original_unlink = Path.unlink
    original_rename = Path.rename
    
    def traced_unlink(self, missing_ok=False):
        print(f"🚨 UNLINK CALLED: {self}")
        import traceback
        traceback.print_stack()
        return original_unlink(self, missing_ok=missing_ok)
    
    def traced_rename(self, target):
        print(f"📁 RENAME/MOVE CALLED: {self} -> {target}")
        if str(self).endswith('.mp4') and 'assets' in str(self):
            print(f"🚨 ASSET FILE BEING MOVED: {self} -> {target}")
            import traceback
            traceback.print_stack()
        return original_rename(self, target)
    
    # Patch file operations
    Path.unlink = traced_unlink
    Path.rename = traced_rename
    
    try:
        print("\n=== GETTING RANDOM VIDEO PATH ===")
        video_path = get_random_video_path_by_examination_alias(
            examination_alias='egd', is_anonymous=False
        )
        print(f"Selected video path: {video_path}")
        print(f"File exists: {video_path.exists()}")
        
        print("\n=== CREATING VIDEO FILE ===")
        video_file = get_default_video_file()
        
        print(f"Video created: {video_file.uuid if video_file else 'None'}")
        
        # Check final state
        final_files = list(asset_dir.glob("*.mp4"))
        print(f"Final video assets: {[f.name for f in final_files]}")
        
        missing_files = set(f.name for f in initial_files) - set(f.name for f in final_files)
        if missing_files:
            print(f"🚨 ASSET FILES DELETED: {missing_files}")
        else:
            print("✅ No asset files were deleted")
            
        # Cleanup
        if video_file:
            print("\n=== CLEANING UP VIDEO FILE ===")
            video_file.delete_with_file()
            
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore original methods
        Path.unlink = original_unlink
        Path.rename = original_rename
        
        print("\n=== FINAL CHECK ===")
        final_files = list(asset_dir.glob("*.mp4"))
        print(f"Final video assets after cleanup: {[f.name for f in final_files]}")

if __name__ == "__main__":
    trace_video_creation()
