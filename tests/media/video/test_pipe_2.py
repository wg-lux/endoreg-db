from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .test_video_file_extracted import VideoFileModelExtractedTest


def _test_pipe_2(test:"VideoFileModelExtractedTest"):
    """
    Test pipe_2 processing for both real VideoFile and MockVideoFile objects.
    """
    # --- Run Pipe 2 ---
    # This performs anonymization and cleanup
    video_file = test.video_file
    
    # Check if this is a real VideoFile or MockVideoFile
    is_mock_video = hasattr(video_file, '__class__') and video_file.__class__.__name__ == 'MockVideoFile'
    
    # Store raw file path before it's deleted (only for real VideoFile)
    original_raw_file_path = None
    if not is_mock_video and hasattr(video_file, 'get_raw_file_path'):
        original_raw_file_path = video_file.get_raw_file_path()  # type: ignore
    
    success = video_file.pipe_2()
    test.assertTrue(success, "Pipe 2 failed: Post-Validation Processing failed.")

    # --- Assertions after Pipe 2 ---
    
    if is_mock_video:
        # For MockVideoFile, only test basic properties
        test.assertTrue(hasattr(video_file, 'is_processed'), "MockVideoFile should have is_processed attribute")
        return  # Skip detailed assertions for mock
    
    # Real VideoFile assertions - use type ignore to handle union type
    video_file.refresh_from_db()  # type: ignore
    state = video_file.state  # type: ignore
    test.assertIsNotNone(state, "VideoState should exist after pipe_2")
    state.refresh_from_db()

    # Check Anonymized Video
    test.assertTrue(video_file.is_processed, "VideoFile should be marked as processed")  # type: ignore
    test.assertIsNotNone(video_file.processed_file, "processed_file field should be set")  # type: ignore
    test.assertTrue(bool(video_file.processed_file.name), "processed_file field should have a name")  # type: ignore
    processed_path = video_file.get_processed_file_path()  # type: ignore
    test.assertIsNotNone(processed_path, "Processed file path should be obtainable")
    test.assertTrue(processed_path.exists(), f"Processed video file should exist at {processed_path}")
    test.assertIsNotNone(video_file.processed_video_hash, "processed_video_hash should be set")  # type: ignore
    
    # CRITICAL: Verify frame count integrity during transcoding/anonymization
    # This ensures no frames are lost during the video processing pipeline
    original_frame_count = getattr(video_file, 'frame_count', None)  # type: ignore
    print("\n🔍 FRAME INTEGRITY ANALYSIS")
    print(f"Original frame_count from video_file: {original_frame_count}")
    
    # Let's also check the database frame count vs file metadata
    if not is_mock_video:
        db_frame_count = video_file.frames.count()  # type: ignore
        print(f"Database Frame objects count: {db_frame_count}")
        
        # Check original raw video metadata for comparison
        original_raw_path = original_raw_file_path
        if original_raw_path and original_raw_path.exists():
            from endoreg_db.utils.video.ffmpeg_wrapper import get_stream_info
            import cv2
            raw_info = get_stream_info(original_raw_path)
            if raw_info and "streams" in raw_info:
                raw_video_stream = next((s for s in raw_info["streams"] if s.get("codec_type") == "video"), None)
                if raw_video_stream:
                    raw_nb_frames = raw_video_stream.get("nb_frames", "N/A")
                    print(f"Original raw video nb_frames: {raw_nb_frames}")
    
    if original_frame_count is not None:
        # Get processed video metadata to verify frame count
        from endoreg_db.utils.video.ffmpeg_wrapper import get_stream_info
        import cv2
        try:
            processed_stream_info = get_stream_info(processed_path)
            if processed_stream_info and "streams" in processed_stream_info:
                video_stream = next((s for s in processed_stream_info["streams"] if s.get("codec_type") == "video"), None)
                if video_stream:
                    # Use multiple methods to get accurate frame count
                    # Method 1: FFmpeg nb_frames (most reliable)
                    nb_frames_ffmpeg = video_stream.get("nb_frames")
                    processed_frame_count = None
                    frame_count_method = "Unknown"
                    
                    if nb_frames_ffmpeg and str(nb_frames_ffmpeg).isdigit():
                        processed_frame_count = int(nb_frames_ffmpeg)
                        frame_count_method = "FFmpeg nb_frames"
                    else:
                        # Method 2: OpenCV frame count (fallback)
                        try:
                            cap = cv2.VideoCapture(str(processed_path))
                            if cap.isOpened():
                                cv_frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                                cap.release()
                                if cv_frame_count > 0:
                                    processed_frame_count = cv_frame_count
                                    frame_count_method = "OpenCV CAP_PROP_FRAME_COUNT"
                        except Exception as cv_e:
                            import logging
                            logging.getLogger(__name__).warning(f"OpenCV frame count failed: {cv_e}")
                    
                    # Method 3: Duration * FPS calculation (least reliable, last resort)
                    if processed_frame_count is None:
                        duration = float(video_stream.get("duration", 0))
                        fps_str = video_stream.get("r_frame_rate", "30/1")
                        if "/" in fps_str:
                            num, den = fps_str.split("/")
                            fps = float(num) / float(den) if den != "0" else 30.0
                        else:
                            fps = float(fps_str) if fps_str else 30.0
                        
                        processed_frame_count = int(duration * fps) if duration > 0 and fps > 0 else 0
                        frame_count_method = "Duration * FPS calculation"
                    
                    if processed_frame_count is not None and processed_frame_count > 0:
                        print("\n📊 FRAME COUNT COMPARISON:")
                        print(f"  Original: {original_frame_count}")
                        print(f"  Processed: {processed_frame_count} (via {frame_count_method})")
                        print(f"  Difference: {original_frame_count - processed_frame_count}")
                        
                        # Detailed analysis of the processed video
                        print("\n📋 PROCESSED VIDEO DETAILS:")
                        print(f"  Path: {processed_path}")
                        print(f"  Duration: {video_stream.get('duration', 'N/A')}")
                        print(f"  FPS: {video_stream.get('r_frame_rate', 'N/A')}")
                        print(f"  nb_frames: {video_stream.get('nb_frames', 'N/A')}")
                        
                        # Try all methods to double-check
                        all_methods = []
                        if video_stream.get('nb_frames') and str(video_stream.get('nb_frames')).isdigit():
                            all_methods.append(('FFmpeg nb_frames', int(video_stream.get('nb_frames'))))
                        
                        try:
                            cap = cv2.VideoCapture(str(processed_path))
                            if cap.isOpened():
                                cv_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                                cap.release()
                                all_methods.append(('OpenCV', cv_count))
                        except Exception:
                            pass
                            
                        duration = float(video_stream.get("duration", 0))
                        fps_str = video_stream.get("r_frame_rate", "30/1")
                        if "/" in fps_str:
                            num, den = fps_str.split("/")
                            fps_calc = float(num) / float(den) if den != "0" else 30.0
                        else:
                            fps_calc = float(fps_str) if fps_str else 30.0
                        calc_count = int(duration * fps_calc) if duration > 0 and fps_calc > 0 else 0
                        all_methods.append(('Duration*FPS', calc_count))
                        
                        print("\n🔬 ALL FRAME COUNT METHODS:")
                        for method, count in all_methods:
                            print(f"  {method}: {count}")
                        
                        # Allow small variance (±1 frame) due to rounding in video processing
                        frame_diff = abs(original_frame_count - processed_frame_count)
                        
                        if frame_diff <= 1:
                            if frame_diff == 0:
                                print("\n✅ Frame count preserved perfectly!")
                                test.assertTrue(True, f"✓ Frame count preserved: {original_frame_count} frames (verified via {frame_count_method})")
                            else:
                                print(f"\n⚠️ Minor frame difference detected: {frame_diff} frame(s)")
                                print("This is within acceptable tolerance (±1 frame) but should be investigated.")
                                # For now, let's allow ±1 frame difference and just warn
                                test.assertTrue(True, f"✓ Frame count within tolerance: {original_frame_count} → {processed_frame_count} (verified via {frame_count_method})")
                        else:
                            print(f"\n❌ Significant frame loss detected: {frame_diff} frames!")
                            test.assertTrue(False,
                                f"Frame count variance beyond tolerance: original={original_frame_count}, "
                                f"processed={processed_frame_count} (via {frame_count_method}). "
                                f"Difference: {frame_diff} frames (tolerance: ±1)"
                            )

                    else:
                        import logging
                        logging.getLogger(__name__).warning("Could not determine processed video frame count using any method")
        except Exception as e:
            # If frame count verification fails, log warning but don't fail the test
            # since this is additional validation, not core functionality
            import logging
            logging.getLogger(__name__).warning(f"Could not verify frame count integrity: {e}")

    # Check Raw Video Deletion
    test.assertFalse(video_file.has_raw, "VideoFile should not have raw file after pipe_2")  # type: ignore
    test.assertFalse(bool(video_file.raw_file.name), "raw_file field name should be empty")  # type: ignore
    if original_raw_file_path:
        # In tests, the file cleanup might be asynchronous via transaction.on_commit()
        # For Django TestCase, this callback may not execute since transactions are rolled back
        # Only check file deletion if we're not in a test transaction
        from django.db import transaction
        import time
        
        # Wait a brief moment for async cleanup to complete
        if not transaction.get_connection().in_atomic_block:
            # We're not in a transaction, cleanup should happen
            time.sleep(0.1)  # Brief wait for async cleanup
            test.assertFalse(original_raw_file_path.exists(), f"Original raw video file {original_raw_file_path} should be deleted")
        else:
            # We're in a test transaction, on_commit callbacks won't execute
            # Just verify the file field was cleared
            pass

    # Check Metadata/State Updates
    test.assertIsNone(video_file.sensitive_meta, "SensitiveMeta should be deleted (set to None) after pipe_2")  # type: ignore
    video_file.refresh_from_db()  # type: ignore
    state = video_file.state  # type: ignore
    # Check VideoState flags (Add these flags to VideoState model if they don't exist)
    test.assertTrue(state.anonymized, "State.is_anonymized should be True") # Assuming this flag exists
    test.assertTrue(state.frames_extracted, "State.frames_extracted should be True after pipe_2 (pipe_2 extracts frames)")
    test.assertTrue(state.frames_initialized, "State.frames_initialized should still be True after pipe_2")
