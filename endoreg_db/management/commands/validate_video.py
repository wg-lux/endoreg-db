from django.core.management.base import BaseCommand
from endoreg_db.models import VideoFile
from endoreg_db.helpers.default_objects import get_latest_segmentation_model
from django.db.models import Q # Add this import

# VideoFile instances have a related 'state' (a VideoState object).
# VideoState uses boolean fields (e.g., state.anonymized, state.initial_prediction_completed)
# to track the processing status, rather than a single status field with choices.
# This command interprets combinations of these boolean fields to determine
# if a video is considered "validated" or "anonymized".
# The VideoState model itself is defined in endoreg_db/models/state/video.py (or similar)
# and does not contain an enum like the previously assumed 'VideoFileStateChoices'.

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
        It interprets the boolean flags in the related VideoState object to determine
        if a video is 'validated' (e.g., initial_prediction_completed and lvs_created are True)
        or 'anonymized' (e.g., anonymized is True).
        """
        #TODO @maxhild here is some ai generated code for now, not validated yet
        verbose = options["verbose"]
        force = options["force"]
        anonymize_option = options["anonymize"]

        if verbose:
            self.stdout.write(self.style.SUCCESS("Starting video validation and/or anonymization process..."))

        # Eager load related state if VideoFile.state is a ForeignKey or OneToOneField
        videos_query = VideoFile.objects.select_related('state').all()

        # Define conditions for "validated" and "anonymized" based on VideoState boolean fields
        # Validated: initial_prediction_completed = True AND lvs_created = True
        q_validated = Q(state__initial_prediction_completed=True, state__lvs_created=True)
        # Anonymized: anonymized = True
        q_anonymized = Q(state__anonymized=True)

        if not force:
            if anonymize_option:
                # If anonymization is the goal, process videos not yet anonymized.
                videos_query = videos_query.exclude(q_anonymized)
            else:
                # If only validation is the goal, process videos not yet validated or anonymized.
                videos_query = videos_query.exclude(q_validated | q_anonymized)
        
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
            state_summary = "N/A"
            if hasattr(video, 'state') and video.state:
                s = video.state
                state_summary = f"anonymized={s.anonymized}, predicted={s.initial_prediction_completed}, lvs_created={s.lvs_created}"
            
            if verbose:
                self.stdout.write(f"Processing video: {video.uuid} (Current state: {state_summary})")
            
            try:
                # Determine if pipe_1 needs to run
                needs_pipe_1 = force
                if not force and hasattr(video, 'state') and video.state:
                    s = video.state
                    is_validated = s.initial_prediction_completed and s.lvs_created
                    is_anonymized = s.anonymized
                    if not (is_validated or is_anonymized):
                        needs_pipe_1 = True
                elif not force: # No state object, assume it needs processing
                    needs_pipe_1 = True


                if needs_pipe_1:
                    if verbose:
                        self.stdout.write(f"Running pipe_1 for video {video.uuid} (force={force})...")
                    if not model_name_for_pipe1 and verbose:
                         self.stdout.write(self.style.WARNING(f"Attempting pipe_1 for {video.uuid} without a specific model name."))
                    
                    success_pipe_1 = video.pipe_1(model_name=model_name_for_pipe1)
                    video.refresh_from_db() 

                    new_state_summary = "N/A"
                    if hasattr(video, 'state') and video.state:
                        s = video.state
                        new_state_summary = f"anonymized={s.anonymized}, predicted={s.initial_prediction_completed}, lvs_created={s.lvs_created}"

                    if not success_pipe_1:
                        raise Exception(f"pipe_1 validation failed for video {video.uuid}. State after attempt: {new_state_summary}")
                    
                    if verbose:
                        self.stdout.write(self.style.SUCCESS(f"Video {video.uuid} successfully passed pipe_1. New state: {new_state_summary}"))
                elif verbose:
                    self.stdout.write(f"Video {video.uuid} already meets validation criteria or is anonymized, skipping pipe_1 (force=False).")

                # Anonymization step
                if anonymize_option:
                    should_anonymize = False
                    current_state_summary_for_anonym = "N/A"
                    if hasattr(video, 'state') and video.state:
                        s = video.state
                        current_state_summary_for_anonym = f"anonymized={s.anonymized}, predicted={s.initial_prediction_completed}, lvs_created={s.lvs_created}"
                        is_validated_for_anonymization = s.initial_prediction_completed and s.lvs_created
                        is_currently_anonymized = s.anonymized
                        if force or (is_validated_for_anonymization and not is_currently_anonymized):
                            should_anonymize = True
                    elif force: # No state, but force anonymize
                        should_anonymize = True


                    if should_anonymize:
                        if verbose:
                            self.stdout.write(f"Attempting to anonymize video: {video.uuid} (force={force}, current_state_before_anonym={current_state_summary_for_anonym}).")
                        
                        if hasattr(video, 'anonymize_video_content'): 
                            video.anonymize_video_content() 
                            video.refresh_from_db() 
                        else:
                            self.stdout.write(self.style.ERROR(f"Video model does not have 'anonymize_video_content' method. Skipping anonymization for {video.uuid}."))
                            # Potentially raise an error or handle as a failure

                        post_anonym_state_summary = "N/A"
                        is_now_anonymized = False
                        if hasattr(video, 'state') and video.state:
                            s = video.state
                            post_anonym_state_summary = f"anonymized={s.anonymized}, predicted={s.initial_prediction_completed}, lvs_created={s.lvs_created}"
                            is_now_anonymized = s.anonymized

                        if is_now_anonymized:
                            if verbose:
                                self.stdout.write(self.style.SUCCESS(f"Video {video.uuid} successfully anonymized. New state: {post_anonym_state_summary}"))
                        else:
                            if hasattr(video, 'anonymize_video_content'): # Only raise if we attempted
                                raise Exception(f"Anonymization called but video is not marked as anonymized for video {video.uuid}. Current state: {post_anonym_state_summary}")
                    
                    elif hasattr(video, 'state') and video.state and video.state.anonymized and verbose: # Already anonymized
                        self.stdout.write(f"Video {video.uuid} is already anonymized.")
                    elif verbose: 
                        self.stdout.write(f"Skipping anonymization for video {video.uuid} (not validated for anonymization, or already anonymized and not forced).")
                
                processed_count += 1
            except Exception as e:
                failed_count += 1
                video.refresh_from_db() 
                error_state_summary = "N/A"
                if hasattr(video, 'state') and video.state:
                    s = video.state
                    error_state_summary = f"anonymized={s.anonymized}, predicted={s.initial_prediction_completed}, lvs_created={s.lvs_created}"
                self.stdout.write(self.style.ERROR(f"Error processing video {video.uuid}: {e}. State after error: {error_state_summary}"))
                # Optionally, explicitly set an error state if the methods don't do it reliably:
                # if hasattr(video, 'state') and video.state: # Further checks would depend on how an error state is defined with booleans
                #     # video.state.set_status(VideoFile.VideoFileStateChoices.ERROR, message=f"Validation command error: {str(e)[:250]}") # Old way
                #     # video.state.save() # New way would involve setting specific boolean flags to indicate error
                pass


        if verbose:
            self.stdout.write(self.style.SUCCESS(f"Video processing finished. Succeeded: {processed_count}, Failed: {failed_count}."))

