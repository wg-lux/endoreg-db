import subprocess
from endoreg_db.models.administration.center import center

# Start process with interactive pipes
proc = subprocess.Popen(
    ["python3", "-i"],  # or your target program
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

proc.stdin.write("print('Trying to extract...')\n")
proc.stdin.flush()

try:
    subprocess.run(["python", "manage.py", "load_center_data"], check=True)
    proc.stdout.write("print('found center')")
except subprocess.CalledProcessError as e:
    proc.stdout.write("print('Didn't find center. Please add it to endoreg_db luxnix or via export DEFAULT_CENTER")
# """
# Future Implementation using dialogue
#"""
# # Send commands as if from terminal
# proc.stdin.write("print('You dont have a default center set up yet. Please enter one here.')\n\nprint('Rule: use_this_format_and_connect_words_with_underscore')")
# proc.stdin.flush()

# # Read responses
# for _ in range(3):
#     line = proc.stdout.readline()
#     print("Selected >>", line.strip())