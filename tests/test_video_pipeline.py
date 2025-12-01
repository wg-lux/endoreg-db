#!/usr/bin/env python3
"""
Corrected Video Import and Anonymization Test Script

This script demonstrates the complete pipeline for importing and processing a video:
1. Video import and metadata extraction
2. Frame extraction and AI processing (Pipe 1)
3. User validation simulation (test_after_pipe_1)
4. Video anonymization (Pipe 2)
"""

from pathlib import Path
from endoreg_db.models import VideoFile
from endoreg_db.services.video_import import import_and_anonymize

# Configuration
DEFAULT_ENDOSCOPY_PROCESSOR_NAME = "olympus_cv_1500"
DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
VIDEO_PATH = Path("/home/admin/dev/endoreg-db/tests/assets/test_outside.mp4")

def main():
    """Execute the complete video processing pipeline."""
    
    print("=== Video Import and Anonymization Pipeline ===")
    
    # Step 1: Import video with complete Pipe 1 processing
    print(f"\n1. Importing video: {VIDEO_PATH}")
    
    if not VIDEO_PATH.exists():
        print(f"ERROR: Video file not found at {VIDEO_PATH}")
        return
    
    try:
        # This now includes Pipe 1 processing automatically
        video_file = import_and_anonymize(
            file_path=VIDEO_PATH,
            center_name=DEFAULT_CENTER_NAME,
            processor_name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
            delete_source=False,  # Keep original for testing
            save_video=True
        )
        
        print(f"✓ Video imported successfully with UUID: {video_file.uuid}")
        
        # Verify Pipe 1 completed
        state = video_file.get_or_create_state()
        print(f"✓ Frames extracted: {state.frames_extracted}")
        print(f"✓ Text metadata extracted: {state.text_meta_extracted}")
        print(f"✓ Initial prediction completed: {state.initial_prediction_completed}")
        print(f"✓ Label video segments created: {state.lvs_created}")
        
        # Check sensitive metadata
        if video_file.sensitive_meta:
            print(f"✓ Sensitive metadata created: {video_file.sensitive_meta.id}")
        else:
            print("⚠ No sensitive metadata found")
        
        # Check label video segments
        segments = video_file.label_video_segments.all()
        print(f"✓ Created {segments.count()} label video segments")
        
    except Exception as e:
        print(f"✗ Error during import: {e}")
        return
    
    # Step 2: Simulate user validation (test_after_pipe_1)
    print(f"\n2. Simulating user validation...")
    
    try:
        # This marks outside segments as validated and sensitive meta as verified
        success = video_file.test_after_pipe_1()
        
        if success:
            print("✓ User validation simulation completed")
            
            # Verify validation state
            video_file.refresh_from_db()
            if video_file.sensitive_meta:
                sm_state = video_file.sensitive_meta.state
                if sm_state:
                    print(f"✓ Sensitive meta verified: {sm_state.is_verified}")
                    print(f"✓ DOB verified: {sm_state.dob_verified}")
                    print(f"✓ Names verified: {sm_state.names_verified}")
        else:
            print("✗ User validation simulation failed")
            return
            
    except Exception as e:
        print(f"✗ Error during validation: {e}")
        return
    
    # Step 3: Run Pipe 2 (Anonymization)
    print(f"\n3. Starting video anonymization (Pipe 2)...")
    
    try:
        # This creates the anonymized video
        success = video_file.pipe_2()
        
        if success:
            print("✓ Video anonymization completed")
            
            # Verify anonymization results
            video_file.refresh_from_db()
            print(f"✓ Video processed: {video_file.is_processed}")
            print(f"✓ Has processed file: {bool(video_file.processed_file)}")
            print(f"✓ Processed video hash: {video_file.processed_video_hash[:8] if video_file.processed_video_hash else 'None'}...")
            
            # Check if raw file was deleted (should be)
            print(f"✓ Raw file deleted: {not video_file.has_raw}")
            
            # Check final state
            final_state = video_file.get_or_create_state()
            print(f"✓ Finally anonymized: {final_state.anonymized}")
            
        else:
            print("✗ Video anonymization failed")
            return
            
    except Exception as e:
        print(f"✗ Error during anonymization: {e}")
        return
    
    print(f"\n=== Pipeline Completed Successfully ===")
    print(f"Video UUID: {video_file.uuid}")
    print(f"Processed file: {video_file.processed_file.name if video_file.processed_file else 'None'}")

if __name__ == "__main__":
    main()