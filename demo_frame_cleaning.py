#!/usr/bin/env python3
"""
Demo script for frame-level video anonymization.

This script demonstrates how to use the frame cleaning functionality
to remove sensitive information from video files.
"""

import sys
from pathlib import Path
import tempfile
import subprocess
import time

# Add lx-anonymizer to path
lx_anonymizer_path = Path(__file__).parent / "lx-anonymizer"
if lx_anonymizer_path.exists():
    sys.path.insert(0, str(lx_anonymizer_path))


def create_demo_video(output_path: Path, duration: int = 3) -> bool:
    """Create a demo video with text overlay for testing."""
    try:
        # Create a simple test video first (without text overlay to avoid FFmpeg issues)
        cmd = [
            'ffmpeg', '-f', 'lavfi', 
            '-i', f'testsrc=duration={duration}:size=640x480:rate=1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f"✓ Created demo video: {output_path}")
            return True
        else:
            print(f"✗ Failed to create demo video: {result.stderr}")
            return False
            
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"✗ FFmpeg error: {e}")
        return False


def demo_frame_cleaning_light():
    """Demonstrate the frame cleaning functionality without heavy GPU loading."""
    print("=" * 60)
    print("FRAME-LEVEL VIDEO ANONYMIZATION DEMO")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
        # Create demo video
        demo_video = tmp_path / "demo_patient_video.mp4"
        print("Creating demo video...")
        if not create_demo_video(demo_video):
            print("Cannot proceed without demo video")
            return
        
        print(f"\nOriginal video size: {demo_video.stat().st_size:,} bytes")
        
        # Simulate frame cleaning process without loading heavy dependencies
        print("\nSimulating frame-level anonymization process...")
        print("1. Extracting frames from video...")
        time.sleep(0.5)
        print("2. Analyzing frames for sensitive content...")
        time.sleep(0.5)
        print("3. Detecting patient data patterns...")
        time.sleep(0.5)
        print("4. Applying anonymization filters...")
        time.sleep(0.5)
        print("5. Reconstructing cleaned video...")
        time.sleep(0.5)
        
        # Copy the demo video to simulate cleaning
        cleaned_video = tmp_path / "demo_patient_video_cleaned.mp4"
        import shutil
        shutil.copy2(demo_video, cleaned_video)
        
        print(f"✓ Simulation completed!")
        print(f"Original video: {demo_video.name}")
        print(f"Cleaned video: {cleaned_video.name}")
        print(f"Cleaned video size: {cleaned_video.stat().st_size:,} bytes")
        
        print("\nNote: This is a simulation. In real usage, the frame cleaner would:")
        print("- Use OCR to detect text in video frames")
        print("- Apply patient data pattern matching")
        print("- Remove or blur frames containing sensitive information")
        print("- Reconstruct video from cleaned frames")


def try_real_frame_cleaning():
    """Try to import and use the real frame cleaning functionality."""
    print("\n" + "=" * 60)
    print("ATTEMPTING REAL FRAME CLEANING")
    print("=" * 60)
    
    try:
        print("Importing frame cleaner...")
        # Import with timeout to avoid hanging
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError("Import timeout")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)  # 10 second timeout
        
        try:
            from lx_anonymizer import frame_cleaner
            from lx_anonymizer.report_reader import ReportReader
            signal.alarm(0)  # Cancel timeout
            print("✓ Successfully imported frame cleaning modules")
            return True
        except TimeoutError:
            print("✗ Import timeout - modules may be loading heavy dependencies")
            return False
        finally:
            signal.alarm(0)  # Make sure to cancel timeout
            
    except Exception as e:
        print(f"✗ Failed to import frame cleaning modules: {e}")
        return False


def demo_cli_usage():
    """Show how to use the CLI interface."""
    print("\n" + "=" * 60)
    print("CLI USAGE EXAMPLES")
    print("=" * 60)
    
    cli_path = lx_anonymizer_path / "lx_anonymizer" / "cli" / "frame_cleaner_cli.py"
    
    print(f"""
You can use the frame cleaner CLI tool like this:

# Clean a single video:
python {cli_path} clean /path/to/video.mp4

# Clean with custom output directory:
python {cli_path} clean /path/to/video.mp4 --output-dir /path/to/cleaned/

# Batch clean multiple videos:
python {cli_path} batch /path/to/videos/ --output-dir /path/to/cleaned/

# Clean with debug output:
python {cli_path} --log-level DEBUG clean /path/to/video.mp4
""")


def demo_django_integration():
    """Show Django integration options."""
    print("\n" + "=" * 60)
    print("DJANGO INTEGRATION")
    print("=" * 60)
    print("""
The frame cleaning is integrated into the Django video import pipeline:

AUTOMATIC INTEGRATION:
When you run:
    python manage.py import_video /path/to/video.mp4

The system will:
1. Import the video file into the database
2. Automatically detect and remove frames with sensitive information
3. Continue with normal processing (segmentation, etc.)

MANUAL DJANGO COMMAND:
You can also run frame cleaning manually:
    python manage.py anonymize_video <video_id>

API INTEGRATION:
The Upload API automatically triggers frame cleaning for uploaded videos.
""")


def main():
    """Main demo function."""
    print("Frame-Level Video Anonymization Demo")
    print("Integrates with lx_anonymizer for sensitive data detection")
    
    # Check if dependencies are available
    try:
        import cv2
        print("✓ OpenCV is available")
    except ImportError:
        print("✗ OpenCV not available")
    
    try:
        import pytesseract
        print("✓ Tesseract is available")
    except ImportError:
        print("✗ Tesseract not available")
    
    # Run lightweight demo first
    demo_frame_cleaning_light()
    
    # Try real frame cleaning if possible
    if try_real_frame_cleaning():
        print("Real frame cleaning modules are available for use")
    else:
        print("Using simulation mode due to import issues")
    
    # Show usage examples
    demo_cli_usage()
    demo_django_integration()


if __name__ == "__main__":
    main()