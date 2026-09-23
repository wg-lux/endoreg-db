# Source - https://stackoverflow.com/q/31793540
# Posted by nonbot, modified by community. See post 'Timeline' for change history
# Retrieved 2026-09-18, License - CC BY-SA 3.0
# run using # Source - https://stackoverflow.com/a/31796491
# Posted by Micah Elliott
# Retrieved 2026-09-18, License - CC BY-SA 3.0

# run with python test_out.py >myoutput.log

import pytest
import os
import subprocess


subprocess.call("pytest")

if __name__ == "__main__":
    pytest.main(args=["-sv", os.path.abspath(__file__)])
