import subprocess
import os

# /home/setup-user/dev/endoreg-db/tests/sensitive/raw_videos/001.mp4
# /home/setup-user/dev/endoreg-db/tests/sensitive/raw_videos/001_short.mp4

def copy_first_minute(input_file, output_file):
    """
    Copies the first minute (60 seconds) of a given MP4 file to a new file.
    
    Parameters:
        input_file (str): Path to the input MP4 file.
        output_file (str): Path to the output MP4 file.
    """
    # Check if the input file exists
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Input file '{input_file}' does not exist.")
    
    # Construct the ffmpeg command
    command = [
        "ffmpeg",
        "-i", input_file,          # Input file
        "-t", "60",                # Duration to copy (60 seconds)
        "-c", "copy",              # Copy codec (no re-encoding)
        output_file                # Output file
    ]

    try:
        # Run the command
        subprocess.run(command, check=True)
        print(f"The first minute of '{input_file}' has been saved to '{output_file}'.")
    except subprocess.CalledProcessError as e:
        print(f"Error during file processing: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

# Example usage
if __name__ == "__main__":
    # Input and output file paths
    input_path = "input.mp4"
    output_path = "output_first_minute.mp4"

    try:
        copy_first_minute(input_path, output_path)
    except Exception as error:
        print(f"Failed: {error}")
