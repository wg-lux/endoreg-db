import logging
import subprocess
from pathlib import Path
from typing import Optional, Iterator, Tuple
import tempfile
import shutil

from ...exceptions import InsufficientStorageError, VideoProcessingError

logger = logging.getLogger(__name__)

class StreamingVideoProcessor:
    """
    Streaming video processor for memory-efficient video anonymization.
    Processes videos in chunks to reduce memory usage and improve performance.
    """
    
    def __init__(self, chunk_duration: int = 30, temp_dir: Optional[Path] = None):
        """
        Initialize the streaming processor.
        
        Args:
            chunk_duration: Duration of each chunk in seconds
            temp_dir: Temporary directory for processing chunks
        """
        self.chunk_duration = chunk_duration
        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir()) / 'video_streaming'
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
    def check_ffmpeg_available(self) -> bool:
        """Check if FFmpeg is available in the system."""
        try:
            subprocess.run(['ffmpeg', '-version'], 
                         capture_output=True, check=True, timeout=10)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def get_video_duration(self, video_path: Path) -> float:
        """Get video duration in seconds using FFprobe."""
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'csv=p=0', str(video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired) as e:
            logger.error(f"Failed to get video duration for {video_path}: {e}")
            raise VideoProcessingError(f"Could not determine video duration: {e}")
    
    def split_video_chunks(self, video_path: Path) -> Iterator[Tuple[Path, float, float]]:
        """
        Split video into chunks for streaming processing.
        
        Args:
            video_path: Path to the input video
            
        Yields:
            Tuple of (chunk_path, start_time, end_time)
        """
        if not self.check_ffmpeg_available():
            raise VideoProcessingError("FFmpeg not available for video processing")
        
        try:
            total_duration = self.get_video_duration(video_path)
            logger.info(f"Video duration: {total_duration:.2f}s, splitting into {self.chunk_duration}s chunks")
            
            chunk_count = 0
            for start_time in range(0, int(total_duration), self.chunk_duration):
                end_time = min(start_time + self.chunk_duration, total_duration)
                
                # Create chunk filename
                chunk_filename = f"chunk_{chunk_count:04d}_{start_time}_{int(end_time)}.mp4"
                chunk_path = self.temp_dir / chunk_filename
                
                # Extract chunk using FFmpeg
                cmd = [
                    'ffmpeg', '-y',  # Overwrite output files
                    '-ss', str(start_time),  # Start time
                    '-i', str(video_path),   # Input file
                    '-t', str(end_time - start_time),  # Duration
                    '-c', 'copy',  # Copy streams without re-encoding for speed
                    '-avoid_negative_ts', 'make_zero',  # Handle timestamp issues
                    str(chunk_path)
                ]
                
                try:
                    logger.debug(f"Creating chunk {chunk_count}: {start_time}s-{end_time}s")
                    result = subprocess.run(cmd, capture_output=True, text=True, 
                                          check=True, timeout=300)  # 5 minute timeout per chunk
                    
                    if chunk_path.exists() and chunk_path.stat().st_size > 0:
                        yield chunk_path, start_time, end_time
                        chunk_count += 1
                    else:
                        logger.warning(f"Chunk {chunk_count} was not created or is empty")
                        
                except subprocess.CalledProcessError as e:
                    logger.error(f"FFmpeg failed for chunk {chunk_count}: {e.stderr}")
                    # Skip this chunk but continue with others
                    continue
                except subprocess.TimeoutExpired:
                    logger.error(f"FFmpeg timeout for chunk {chunk_count}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in video chunking: {e}")
            raise VideoProcessingError(f"Video chunking failed: {e}")
    
    def process_chunk_anonymization(self, chunk_path: Path, anonymizer_func, **kwargs) -> Path:
        """
        Process a single chunk with the anonymization function.
        
        Args:
            chunk_path: Path to the video chunk
            anonymizer_func: Function to anonymize the chunk
            **kwargs: Additional arguments for the anonymizer
            
        Returns:
            Path to the anonymized chunk
        """
        try:
            output_path = chunk_path.with_suffix('.anonymized.mp4')
            
            # Call the anonymization function
            result = anonymizer_func(chunk_path, output_path, **kwargs)
            
            if isinstance(result, Path):
                return result
            elif result is True and output_path.exists():
                return output_path
            else:
                raise VideoProcessingError(f"Anonymization failed for chunk {chunk_path}")
                
        except Exception as e:
            logger.error(f"Chunk anonymization failed for {chunk_path}: {e}")
            raise VideoProcessingError(f"Chunk processing failed: {e}")
    
    def merge_chunks(self, chunk_paths: list[Path], output_path: Path) -> Path:
        """
        Merge anonymized chunks back into a single video.
        
        Args:
            chunk_paths: List of paths to anonymized chunks
            output_path: Path for the final merged video
            
        Returns:
            Path to the merged video
        """
        if not chunk_paths:
            raise VideoProcessingError("No chunks to merge")
        
        try:
            # Check storage space before merging
            total_chunk_size = sum(chunk.stat().st_size for chunk in chunk_paths if chunk.exists())
            free_space = shutil.disk_usage(output_path.parent).free
            
            if free_space < total_chunk_size * 1.2:  # 20% safety margin
                raise InsufficientStorageError(
                    f"Insufficient space for merging. Required: {total_chunk_size * 1.2 / 1e9:.1f} GB, "
                    f"Available: {free_space / 1e9:.1f} GB",
                    required_space=int(total_chunk_size * 1.2),
                    available_space=free_space
                )
            
            # Create concat file for FFmpeg
            concat_file = self.temp_dir / f"concat_{output_path.stem}.txt"
            
            with open(concat_file, 'w') as f:
                for chunk_path in chunk_paths:
                    if chunk_path.exists():
                        # Use relative paths in concat file for better portability
                        f.write(f"file '{chunk_path.name}'\n")
            
            # Merge using FFmpeg concat demuxer
            cmd = [
                'ffmpeg', '-y',  # Overwrite output
                '-f', 'concat',  # Use concat demuxer
                '-safe', '0',    # Allow unsafe file paths
                '-i', str(concat_file),  # Input concat file
                '-c', 'copy',    # Copy streams without re-encoding
                str(output_path)
            ]
            
            logger.info(f"Merging {len(chunk_paths)} chunks into {output_path}")
            
            # Change working directory to temp_dir for relative paths
            result = subprocess.run(cmd, cwd=str(self.temp_dir), 
                                  capture_output=True, text=True, check=True, timeout=600)
            
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise VideoProcessingError("Merged video was not created or is empty")
            
            logger.info(f"Successfully merged video: {output_path}")
            
            # Clean up concat file
            concat_file.unlink(missing_ok=True)
            
            return output_path
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg merge failed: {e.stderr}")
            raise VideoProcessingError(f"Video merging failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg merge timeout")
            raise VideoProcessingError("Video merging timed out")
        except Exception as e:
            logger.error(f"Unexpected error during merge: {e}")
            raise VideoProcessingError(f"Video merging failed: {e}")
    
    def process_video_streaming(self, input_path: Path, output_path: Path, 
                              anonymizer_func, progress_callback=None, **kwargs) -> Path:
        """
        Process a video using streaming approach for memory efficiency.
        
        Args:
            input_path: Path to input video
            output_path: Path for output video
            anonymizer_func: Function to anonymize video chunks
            progress_callback: Optional callback for progress updates
            **kwargs: Additional arguments for anonymizer
            
        Returns:
            Path to the processed video
        """
        processed_chunks = []
        total_chunks = 0
        
        try:
            logger.info(f"Starting streaming video processing: {input_path} -> {output_path}")
            
            # First pass: count total chunks for progress tracking
            chunk_list = list(self.split_video_chunks(input_path))
            total_chunks = len(chunk_list)
            
            if total_chunks == 0:
                raise VideoProcessingError("No chunks were created from the input video")
            
            logger.info(f"Processing {total_chunks} chunks")
            
            # Process each chunk
            for i, (chunk_path, start_time, end_time) in enumerate(chunk_list):
                try:
                    logger.debug(f"Processing chunk {i+1}/{total_chunks}: {chunk_path}")
                    
                    # Process the chunk
                    processed_chunk = self.process_chunk_anonymization(
                        chunk_path, anonymizer_func, **kwargs
                    )
                    processed_chunks.append(processed_chunk)
                    
                    # Update progress
                    if progress_callback:
                        progress = int((i + 1) / total_chunks * 80)  # Reserve 20% for merging
                        progress_callback(progress, f"Processed chunk {i+1}/{total_chunks}")
                    
                    # Clean up original chunk to save space
                    chunk_path.unlink(missing_ok=True)
                    
                except Exception as e:
                    logger.error(f"Failed to process chunk {i}: {e}")
                    # Clean up failed chunk
                    chunk_path.unlink(missing_ok=True)
                    # Continue with other chunks
                    continue
            
            if not processed_chunks:
                raise VideoProcessingError("No chunks were successfully processed")
            
            # Update progress for merging phase
            if progress_callback:
                progress_callback(80, f"Merging {len(processed_chunks)} processed chunks...")
            
            # Merge processed chunks
            final_output = self.merge_chunks(processed_chunks, output_path)
            
            # Final progress update
            if progress_callback:
                progress_callback(100, "Video processing completed")
            
            logger.info(f"Streaming video processing completed: {final_output}")
            return final_output
            
        except (InsufficientStorageError, VideoProcessingError):
            # Re-raise these specific errors as-is
            raise
        except Exception as e:
            logger.error(f"Streaming video processing failed: {e}")
            raise VideoProcessingError(f"Streaming processing failed: {e}")
        finally:
            # Clean up all temporary chunks
            self.cleanup_chunks(processed_chunks)
    
    def cleanup_chunks(self, chunk_paths: list[Path]) -> None:
        """Clean up temporary chunk files."""
        for chunk_path in chunk_paths:
            if chunk_path and chunk_path.exists():
                try:
                    chunk_path.unlink()
                    logger.debug(f"Cleaned up chunk: {chunk_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up chunk {chunk_path}: {e}")
    
    def cleanup_temp_dir(self) -> None:
        """Clean up the entire temporary directory."""
        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                logger.debug(f"Cleaned up temp directory: {self.temp_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean up temp directory {self.temp_dir}: {e}")