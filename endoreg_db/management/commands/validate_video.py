from django.core.management.base import BaseCommand
from endoreg_db.models import VideoFile # MODIFIED: Removed ModelMeta import
from endoreg_db.helpers.default_objects import get_latest_segmentation_model
# Assuming VideoFile.VideoFileStateChoices is how states are defined, e.g., an inner class on VideoFile
# e.g., class VideoFile(models.Model):
#           class VideoFileStateChoices(models.TextChoices):
#               INITIALIZED = 'INIT', 'Initialized'
#               VALIDATED = 'VALID', 'Validated'
#               ANONYMIZED = 'ANON', 'Anonymized'
#               ERROR = 'ERR', 'Error'
#           # ... and a field like:
#           # status = models.CharField(max_length=5, choices=VideoFileStateChoices.choices, default=VideoFileStateChoices.INITIALIZED)
#           # or through a related state model:
#           # state = models.OneToOneField(VideoFileState, ...)

class Command(BaseCommand):
    help = "Data extraction and validation of video files in the database and updating their states accordingly."
    
    def add_arguments(self, parser):
        """
        Adds command-line arguments for verbose output, forced revalidation, and anonymization.
        
        This method configures the management command to accept optional flags:
        --verbose for detailed output, --force to revalidate all videos regardless of status,
        and --anonymize to anonymize video files during processing.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force revalidation of all video files, even if they are already validated.",
        )
        parser.add_argument(
            "--anonymize",
            action="store_true",
            help="Anonymize video files.",
        )

    def handle(self, *args, **options):
        """
        Validates video files stored in the database and updates their validation states.
        
        This method processes video files according to the provided command-line options,
        such as verbose output, forced revalidation, or anonymization.
        """
        #TODO @maxhild here is some ai generated code for now, not validated yet
        verbose = options["verbose"]
        force = options["force"]
        anonymize_option = options["anonymize"]

        if verbose:
            self.stdout.write(self.style.SUCCESS("Starting video validation and/or anonymization process..."))

        # Eager load related state if VideoFile.state is a ForeignKey or OneToOneField
        videos_query = VideoFile.objects.select_related('state').all()

        if not force:
            if anonymize_option:
                # If anonymization is the goal, process videos not yet anonymized.
                # Assumes 'ANONYMIZED' is a status string in VideoFile.state.status
                # and VideoFileStateChoices is an enum/choices class on VideoFile model
                videos_query = videos_query.exclude(state__status=VideoFile.VideoFileStateChoices.ANONYMIZED)
            else:
                # If only validation is the goal, process videos not yet validated (or anonymized).
                videos_query = videos_query.exclude(state__status__in=[
                    VideoFile.VideoFileStateChoices.VALIDATED,
                    VideoFile.VideoFileStateChoices.ANONYMIZED
                ])
        
        videos_to_process = list(videos_query)

        if not videos_to_process:
            if verbose:
                self.stdout.write(self.style.SUCCESS("No videos found requiring processing with the current options."))
            return

        processed_count = 0
        failed_count = 0
        model_name_for_pipe1 = None

        try:
            # Attempt to get the model for pipe_1. This might be optional for pipe_1.
            ai_model_meta = get_latest_segmentation_model()
            model_name_for_pipe1 = ai_model_meta.model.name
            if verbose:
                self.stdout.write(self.style.SUCCESS(f"Using model '{model_name_for_pipe1}' for pipe_1 processing."))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not retrieve segmentation model: {e}. pipe_1 might proceed without a specific model or use a default."))
            # model_name_for_pipe1 can remain None if pipe_1 handles it, or set a default if known.

        for video in videos_to_process:
            current_status_display = video.state.status if hasattr(video, 'state') and video.state else 'N/A'
            if verbose:
                self.stdout.write(f"Processing video: {video.uuid} (Current state: {current_status_display})")
            
            try:
                # Determine if pipe_1 needs to run
                needs_pipe_1 = force or not (hasattr(video, 'state') and video.state and video.state.status in [
                    VideoFile.VideoFileStateChoices.VALIDATED,
                    VideoFile.VideoFileStateChoices.ANONYMIZED
                ])

                if needs_pipe_1:
                    if verbose:
                        self.stdout.write(f"Running pipe_1 for video {video.uuid} (force={force})...")
                    if not model_name_for_pipe1 and verbose:
                         self.stdout.write(self.style.WARNING(f"Attempting pipe_1 for {video.uuid} without a specific model name."))
                    
                    # Assuming pipe_1 is a method on the VideoFile instance
                    # and it updates the video's state internally.
                    success_pipe_1 = video.pipe_1(model_name=model_name_for_pipe1)
                    video.refresh_from_db() # Refresh to get the latest state updated by pipe_1

                    if not success_pipe_1:
                        # Check state after pipe_1 failure, it might have set an error state
                        final_state_after_pipe1_fail = video.state.status if hasattr(video, 'state') and video.state else 'N/A'
                        raise Exception(f"pipe_1 validation failed for video {video.uuid}. State after attempt: {final_state_after_pipe1_fail}")
                    
                    if verbose:
                        self.stdout.write(self.style.SUCCESS(f"Video {video.uuid} successfully passed pipe_1. New state: {video.state.status if hasattr(video, 'state') and video.state else 'N/A'}"))
                elif verbose:
                    self.stdout.write(f"Video {video.uuid} already meets validation criteria, skipping pipe_1 (force=False).")

                # Anonymization step
                if anonymize_option:
                    is_validated_or_anonymized_by_pipe1 = hasattr(video, 'state') and video.state and video.state.status in [VideoFile.VideoFileStateChoices.VALIDATED, VideoFile.VideoFileStateChoices.ANONYMIZED]
                    is_currently_anonymized = hasattr(video, 'state') and video.state and video.state.status == VideoFile.VideoFileStateChoices.ANONYMIZED

                    # Anonymize if forced, OR if it's (now or previously) validated and not yet anonymized.
                    if force or (is_validated_or_anonymized_by_pipe1 and not is_currently_anonymized):
                        if verbose:
                            self.stdout.write(f"Attempting to anonymize video: {video.uuid} (force={force}, current_state={video.state.status if hasattr(video, 'state') and video.state else 'N/A'}).")
                        
                        # Replace with the actual anonymization method on VideoFile.
                        # This method should also update the state internally.
                        # Example: video.anonymize_video_content() 
                        if hasattr(video, 'anonymize_video_content'): # Check if method exists
                            video.anonymize_video_content() 
                            video.refresh_from_db() # Get state after anonymization
                        else:
                            self.stdout.write(self.style.ERROR(f"Video model does not have 'anonymize_video_content' method. Skipping anonymization for {video.uuid}."))
                            # Potentially raise an error or handle as a failure depending on requirements

                        if hasattr(video, 'state') and video.state and video.state.status == VideoFile.VideoFileStateChoices.ANONYMIZED:
                            if verbose:
                                self.stdout.write(self.style.SUCCESS(f"Video {video.uuid} successfully anonymized. New state: {video.state.status}"))
                        else:
                            # If anonymize_video_content was called but state isn't ANONYMIZED
                            if hasattr(video, 'anonymize_video_content'):
                                raise Exception(f"Anonymization called but did not set state to ANONYMIZED for video {video.uuid}. Current state: {video.state.status if hasattr(video, 'state') and video.state else 'N/A'}")
                    
                    elif is_currently_anonymized and verbose:
                        self.stdout.write(f"Video {video.uuid} is already anonymized.")
                    elif verbose: 
                        self.stdout.write(f"Skipping anonymization for video {video.uuid} (not validated, or already anonymized and not forced).")
                
                processed_count += 1
            except Exception as e:
                failed_count += 1
                video.refresh_from_db() # Get potentially updated error state
                error_state_display = video.state.status if hasattr(video, 'state') and video.state else 'N/A'
                self.stdout.write(self.style.ERROR(f"Error processing video {video.uuid}: {e}. State after error: {error_state_display}"))
                # Optionally, explicitly set an error state if the methods don't do it reliably:
                # if hasattr(video, 'state') and video.state and video.state.status != VideoFile.VideoFileStateChoices.ERROR:
                #     video.state.set_status(VideoFile.VideoFileStateChoices.ERROR, message=f"Validation command error: {str(e)[:250]}")
                #     video.state.save()

        if verbose:
            self.stdout.write(self.style.SUCCESS(f"Video processing finished. Succeeded: {processed_count}, Failed: {failed_count}."))

