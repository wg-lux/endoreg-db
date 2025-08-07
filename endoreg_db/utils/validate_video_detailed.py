#!/usr/bin/env python3
"""
Comprehensive video file validation script for debugging streaming issues.
Tests file integrity, codec compatibility, and streaming capabilities.
"""

import os
import sys
import subprocess
from pathlib import Path
import json
import logging

# Dynamically determine the Django project path
# previously resolved to "/home/admin/test/lx-annotate/endoreg-db" (endoreg-db project directory)
project_path = os.environ.get(
    'ENDOREG_DJANGO_PROJECT_PATH',
    str(Path(__file__).resolve().parent.parent.parent)
)
sys.path.insert(0, project_path)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dev.dev_settings')


import django
django.setup()

from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


def check_ffmpeg_available():
    """Check if FFmpeg is available for video analysis."""
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                               capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def analyze_video_with_ffmpeg(video_path):
    """
    Use FFmpeg to analyze video file integrity and get detailed information.
    """
    analysis_result = {
        'file_readable': False,
        'has_video_stream': False,
        'has_audio_stream': False,
        'duration': None,
        'codec': None,
        'resolution': None,
        'frame_count': None,
        'errors': [],
        'warnings': []
    }
    
    try:
        # First, try to probe the file
        print(f"📊 Analyzing video with FFprobe: {video_path}")
        probe_cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json', 
            '-show_format', '-show_streams', str(video_path)
        ]
        
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            analysis_result['file_readable'] = True
            probe_data = json.loads(result.stdout)
            
            # Analyze streams
            if 'streams' in probe_data:
                for stream in probe_data['streams']:
                    if stream.get('codec_type') == 'video':
                        analysis_result['has_video_stream'] = True
                        analysis_result['codec'] = stream.get('codec_name')
                        analysis_result['resolution'] = f"{stream.get('width', '?')}x{stream.get('height', '?')}"
                        analysis_result['frame_count'] = stream.get('nb_frames')
                    elif stream.get('codec_type') == 'audio':
                        analysis_result['has_audio_stream'] = True
            
            # Get format info
            if 'format' in probe_data:
                analysis_result['duration'] = float(probe_data['format'].get('duration', 0))
        else:
            analysis_result['errors'].append(f"FFprobe failed: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        analysis_result['errors'].append("FFprobe timed out - file might be severely corrupted")
    except json.JSONDecodeError:
        analysis_result['errors'].append("FFprobe returned invalid JSON")
    except Exception as e:
        analysis_result['errors'].append(f"FFprobe analysis failed: {e}")
    
    # Try to validate file integrity
    try:
        print("🔍 Testing video integrity...")
        integrity_cmd = [
            'ffmpeg', '-v', 'error', '-i', str(video_path), 
            '-f', 'null', '-', '-t', '5'  # Test first 5 seconds
        ]
        
        result = subprocess.run(integrity_cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print("✅ Video integrity test passed")
        else:
            analysis_result['errors'].append(f"Integrity test failed: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        analysis_result['errors'].append("Integrity test timed out")
    except Exception as e:
        analysis_result['errors'].append(f"Integrity test failed: {e}")
    
    return analysis_result


def test_video_streaming_compatibility(video_path):
    """
    Test if video is compatible with web streaming (H.264 baseline profile recommended).
    """
    compatibility_result = {
        'web_compatible': False,
        'streaming_friendly': False,
        'needs_conversion': False,
        'recommendations': []
    }
    
    try:
        # Check codec and profile
        cmd = [
            'ffprobe', '-v', 'quiet', '-select_streams', 'v:0', 
            '-show_entries', 'stream=codec_name,profile,level,pix_fmt',
            '-of', 'csv=p=0', str(video_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            codec_info = result.stdout.strip().split(',')
            if len(codec_info) >= 1:
                codec = codec_info[0]
                
                if codec == 'h264':
                    compatibility_result['web_compatible'] = True
                    
                    # Check if moov atom is at the beginning (for streaming)
                    moov_cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format', 
                               '-of', 'csv=p=0', str(video_path)]
                    moov_result = subprocess.run(moov_cmd, capture_output=True, text=True, timeout=10)
                    
                    if 'mov,mp4' in moov_result.stdout:
                        compatibility_result['streaming_friendly'] = True
                    else:
                        compatibility_result['recommendations'].append(
                            "Consider re-encoding with 'faststart' flag for better streaming"
                        )
                        
                elif codec in ['hevc', 'h265']:
                    compatibility_result['recommendations'].append(
                        "HEVC/H.265 codec may not be supported in all browsers"
                    )
                    compatibility_result['needs_conversion'] = True
                else:
                    compatibility_result['recommendations'].append(
                        f"Codec '{codec}' may not be web-compatible. Consider H.264"
                    )
                    compatibility_result['needs_conversion'] = True
                    
    except Exception as e:
        compatibility_result['recommendations'].append(f"Could not analyze compatibility: {e}")
    
    return compatibility_result


def test_django_video_access(video_id):
    """
    Test accessing the video through Django ORM and methods.
    """
    access_result = {
        'orm_accessible': False,
        'has_raw_file': False,
        'has_processed_file': False,
        'active_file_path': None,
        'file_size': 0,
        'errors': []
    }
    
    try:
        video = VideoFile.objects.get(pk=video_id)
        access_result['orm_accessible'] = True
        
        # Check raw file
        if hasattr(video, 'raw_file') and video.raw_file:
            try:
                raw_path = video.raw_file.path
                if Path(raw_path).exists():
                    access_result['has_raw_file'] = True
                    access_result['active_file_path'] = raw_path
                    access_result['file_size'] = Path(raw_path).stat().st_size
            except Exception as e:
                access_result['errors'].append(f"Raw file access error: {e}")
        
        # Check processed file
        if hasattr(video, 'processed_file') and video.processed_file:
            try:
                processed_path = video.processed_file.path
                if Path(processed_path).exists():
                    access_result['has_processed_file'] = True
                    if not access_result['active_file_path']:
                        access_result['active_file_path'] = processed_path
                        access_result['file_size'] = Path(processed_path).stat().st_size
            except Exception as e:
                access_result['errors'].append(f"Processed file access error: {e}")
        
        # Test video streaming methods
        try:
            if hasattr(video, 'get_active_file_path'):
                active_path = video.get_active_file_path()
                if active_path and Path(active_path).exists():
                    access_result['active_file_path'] = str(active_path)
                    access_result['file_size'] = Path(active_path).stat().st_size
        except Exception as e:
            access_result['errors'].append(f"Active file path method error: {e}")
            
    except VideoFile.DoesNotExist:
        access_result['errors'].append(f"Video with ID {video_id} not found in database")
    except Exception as e:
        access_result['errors'].append(f"Django access error: {e}")
    
    return access_result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Comprehensive video file validation script for debugging streaming issues.")
    parser.add_argument('video_id', nargs='?', type=int, default=5, help='ID of the video to validate (default: 5)')
    args = parser.parse_args()

    video_id = args.video_id

    print("🎬 COMPREHENSIVE VIDEO VALIDATION")
    print("=" * 50)
    print(f"Testing video ID: {video_id}")
    print()
    
    # Test Django access
    print("1️⃣ Testing Django ORM Access...")
    django_result = test_django_video_access(video_id)
    
    if django_result['orm_accessible']:
        print("✅ Video found in database")
        print(f"📁 Active file path: {django_result['active_file_path']}")
        print(f"📏 File size: {django_result['file_size'] / (1024*1024):.1f} MB")
        
        if django_result['errors']:
            for error in django_result['errors']:
                print(f"⚠️  {error}")
    else:
        print("❌ Video not accessible through Django")
        for error in django_result['errors']:
            print(f"❌ {error}")
        return
    
    # Get video file path for further testing
    video_path = django_result['active_file_path']
    if not video_path or not Path(video_path).exists():
        print("❌ No valid video file path found")
        return
    
    print("\n2️⃣ Testing File System Access...")
    file_path = Path(video_path)
    print(f"📁 Path: {file_path}")
    print(f"✅ File exists: {file_path.exists()}")
    print(f"📏 Size: {file_path.stat().st_size / (1024*1024):.1f} MB")
    print(f"🔑 Readable: {os.access(file_path, os.R_OK)}")
    
    # Check FFmpeg availability
    print("\n3️⃣ Checking FFmpeg Availability...")
    if not check_ffmpeg_available():
        print("❌ FFmpeg not available - cannot perform detailed video analysis")
        print("💡 Install FFmpeg: sudo apt-get install ffmpeg")
        return
    else:
        print("✅ FFmpeg is available")
    
    # Analyze video with FFmpeg
    print("\n4️⃣ Video Analysis with FFmpeg...")
    analysis = analyze_video_with_ffmpeg(video_path)
    
    if analysis['file_readable']:
        print("✅ File is readable by FFmpeg")
        print(f"🎥 Has video stream: {analysis['has_video_stream']}")
        print(f"🔊 Has audio stream: {analysis['has_audio_stream']}")
        print(f"⏱️  Duration: {analysis['duration']:.2f}s" if analysis['duration'] else "⏱️  Duration: Unknown")
        print(f"🎬 Codec: {analysis['codec'] or 'Unknown'}")
        print(f"📐 Resolution: {analysis['resolution'] or 'Unknown'}")
        print(f"🖼️  Frame count: {analysis['frame_count'] or 'Unknown'}")
    else:
        print("❌ File is NOT readable by FFmpeg")
    
    if analysis['errors']:
        print("\n❌ Errors found:")
        for error in analysis['errors']:
            print(f"   • {error}")
    
    if analysis['warnings']:
        print("\n⚠️  Warnings:")
        for warning in analysis['warnings']:
            print(f"   • {warning}")
    
    # Test streaming compatibility
    print("\n5️⃣ Testing Web Streaming Compatibility...")
    compatibility = test_video_streaming_compatibility(video_path)
    
    print(f"🌐 Web compatible: {compatibility['web_compatible']}")
    print(f"📡 Streaming friendly: {compatibility['streaming_friendly']}")
    print(f"🔄 Needs conversion: {compatibility['needs_conversion']}")
    
    if compatibility['recommendations']:
        print("\n💡 Recommendations:")
        for rec in compatibility['recommendations']:
            print(f"   • {rec}")
    
    # Final diagnosis
    print("\n6️⃣ DIAGNOSIS")
    print("=" * 30)
    
    if not analysis['file_readable']:
        print("🔴 CRITICAL: Video file is corrupted or unreadable")
        print("📋 Action: Replace or re-encode the video file")
    elif not analysis['has_video_stream']:
        print("🔴 CRITICAL: No video stream found")
        print("📋 Action: Check if file is actually a video")
    elif not compatibility['web_compatible']:
        print("🟡 WARNING: Video may not be web-compatible")
        print("📋 Action: Consider re-encoding to H.264")
    elif not compatibility['streaming_friendly']:
        print("🟡 WARNING: Video not optimized for streaming")
        print("📋 Action: Re-encode with faststart flag")
    else:
        print("🟢 GOOD: Video appears to be valid and web-compatible")
        print("📋 Issue likely in Django streaming view or network connection")
    
    print("\n🔧 Suggested fixes for streaming issues:")
    print("1. Check Django VideoStreamView implementation")
    print("2. Verify file permissions (readable by web server)")
    print("3. Test with a different video file")
    print("4. Check browser developer tools for specific errors")
    print("5. Consider re-encoding the video:")
    print(f"   ffmpeg -i '{video_path}' -c:v libx264 -profile:v baseline -level 3.0 -movflags faststart output.mp4")


if __name__ == "__main__":
    main()