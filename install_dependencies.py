import platform
import subprocess
import sys


def install_requirements(requirements_file):
    """Install dependencies from the given requirements file."""
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", requirements_file]
    )


def main():
    """Detect the system platform and install the appropriate requirements."""
    system = platform.system()
    is_mac = system == "Darwin"  # macOS
    is_linux = system == "Linux"  # Linux

    if is_mac:
        print("Detected macOS. Installing CPU/MPS dependencies...")
        install_requirements("requirements-cpu.txt")
    elif is_linux:
        print("Detected Linux. Installing GPU dependencies...")
        install_requirements("requirements-gpu.txt")
    else:
        print(f"Unsupported system: {system}")
        sys.exit(1)


if __name__ == "__main__":
    main()
