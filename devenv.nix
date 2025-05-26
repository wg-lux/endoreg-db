{ pkgs, lib, config, inputs, baseBuildInputs, ... }:
let
  # --- Project Configuration ---
  DJANGO_MODULE = "endoreg_db";
  host = "localhost";
  port = "8188";

  # --- Directory Structure ---
  dataDir = "data";
  importDir = "${dataDir}/import";
  importVideoDir = "${importDir}/video";
  importReportDir = "${importDir}/report";
  importLegacyAnnotationDir = "${importDir}/legacy_annotations";
  exportDir = "${dataDir}/export";
  exportFramesRootDir = "${exportDir}/frames";
  exportFramesSampleExportDir = "${exportFramesRootDir}/test_outputs";
  modelDir = "${dataDir}/models";
  confDir = "./conf"; # Define confDir here

  python = pkgs.python312Full;
  # Explicitly define the uv package
  uvPackage = pkgs.uv;
  buildInputs = with pkgs; [
    python
    stdenv.cc.cc
    tesseract
    glib
    openssh
    cmake
    gcc
    pkg-config
    protobuf
    libglvnd
  ];
  runtimePackages = with pkgs; [
    cudaPackages.cuda_nvcc # Needed for runtime? Check dependencies
    stdenv.cc.cc
    ffmpeg-headless.bin
    tesseract
    uvPackage # Add uvPackage to runtime packages if needed elsewhere, or just for devenv internal use
    libglvnd # Add libglvnd for libGL.so.1
    glib
    zlib
  ];

  _module.args.buildInputs = baseBuildInputs;

  lx-anonymizer-src = pkgs.fetchGit {
    url = "https://github.com/wg-lux/lx-anonymizer";
    ref = "prototype";
    # If you know the specific revision, it's better to use rev for reproducibility
    # rev = "abcdef1234567890"; 
  };

  imports = [ 
    "${lx-anonymizer-src}/devenv.nix"
  ]; 

in 
{

  # A dotenv file was found, while dotenv integration is currently not enabled.
  dotenv.enable = true;
  dotenv.disableHint = true;

  packages = runtimePackages ++ buildInputs;

  env = {
    LD_LIBRARY_PATH = lib.makeLibraryPath buildInputs + ":/run/opengl-driver/lib:/run/opengl-driver-32/lib";
  };

  languages.python = {
    enable = true;
    uv = {
      enable = true;
      # Use the explicitly defined uv package
      package = uvPackage;
      sync.enable = true;
    };
  };

  scripts = {
    export-nix-vars.exec = ''
      cat > .devenv-vars.json << EOF
      {
        "DJANGO_MODULE": "${DJANGO_MODULE}",
        "HOST": "${host}",
        "PORT": "${port}",
        "CONF_DIR": "${confDir}",
        "HOME_DIR": "$HOME",
        "WORKING_DIR": "$PWD"
      }
      EOF
      echo "Exported Nix variables to .devenv-vars.json"
    '';
    
    env-setup.exec = ''
    export LD_LIBRARY_PATH="${
      with pkgs;
      lib.makeLibraryPath buildInputs
    }:/run/opengl-driver/lib:/run/opengl-driver-32/lib"
    '';

    hello.package = pkgs.zsh;
    hello.exec = "uv run python hello.py";
    runtests.package = pkgs.zsh;
    runtests.exec = "uv run python runtests.py";
    runtests-media.exec = "uv run python runtests.py 'media'";
    runtests-dataloader.exec = "uv run python runtests.py 'dataloader'";
    runtests-other.exec = "uv run python runtests.py 'other'";
    runtests-helpers.exec = "uv run python runtests.py 'helpers'";
    runtests-administration.exec = "uv run python runtests.py 'administration'";
    runtests-medical.exec = "uv run python runtests.py 'medical'";
    pyshell.exec = "uv run python manage.py shell";
  };

  tasks = {
    "env:build" = {
      description = "Generate/update .env file with secrets and config";
      exec = "export-nix-vars && uv run env_setup.py";
    };
    "env:clean" = {
      description = "Remove the uv virtual environment and lock file for a clean sync";
      exec = ''
        echo "Removing uv virtual environment: .devenv/state/venv"
        rm -rf .devenv/state/venv
        echo "Removing uv lock file: uv.lock"
        rm -f uv.lock
        echo "Environment cleaned. Re-enter the shell (e.g., 'exit' then 'devenv up') to trigger uv sync."
      '';
    };
  
  };

  processes = {
  };

  enterShell = ''
    # Clone or pull lx-anonymizer
    LX_ANONYMIZER_DIR="lx-anonymizer"
    LX_ANONYMIZER_REPO="https://github.com/wg-lux/lx-anonymizer"
    LX_ANONYMIZER_BRANCH="prototype"

    if [ -d "$LX_ANONYMIZER_DIR" ]; then
      echo "lx-anonymizer directory exists. Pulling latest changes from $LX_ANONYMIZER_BRANCH..."
      (cd "$LX_ANONYMIZER_DIR" && git fetch origin && git checkout "$LX_ANONYMIZER_BRANCH" && git reset --hard "origin/$LX_ANONYMIZER_BRANCH")
    else
      echo "lx-anonymizer directory does not exist. Cloning repository..."
      git clone -b "$LX_ANONYMIZER_BRANCH" "$LX_ANONYMIZER_REPO" "$LX_ANONYMIZER_DIR"
    fi

    export SYNC_CMD="uv sync"

    # Ensure dependencies are synced using uv
    # Check if venv exists. If not, run sync verbosely. If it exists, sync quietly.
    if [ ! -d ".devenv/state/venv" ]; then
       echo "Virtual environment not found. Running initial uv sync..."
       $SYNC_CMD || echo "Error: Initial uv sync failed. Please check network and pyproject.toml."
    else
       # Sync quietly if venv exists
       echo "Syncing Python dependencies with uv..."
       $SYNC_CMD --quiet || echo "Warning: uv sync failed. Environment might be outdated."
    fi

    # Activate Python virtual environment managed by uv
    ACTIVATED=false
    if [ -f ".devenv/state/venv/bin/activate" ]; then
      source .devenv/state/venv/bin/activate
      ACTIVATED=true
      echo "Virtual environment activated."
    else
      echo "Warning: uv virtual environment activation script not found. Run 'devenv task run env:clean' and re-enter shell."
    fi

    # . .devenv/state/venv/bin/activate
    # runtests-dataloader
    # hello
  '';

  enterTest = ''
    nvcc -V
  '';
}
