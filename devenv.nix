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

  # Pin to specific Python 3.12 version to match pyproject.toml
  python = pkgs.python312; #known devenv issue with python3Packages since python3Full was deprecated
  uvPackage = pkgs.uv;
  
  buildInputs = with pkgs; [
    python312
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
    stdenv.cc.cc
    ffmpeg-headless.bin
    tesseract
    uvPackage
    libglvnd # Add libglvnd for libGL.so.1
    glib
    zlib
    ollama.out
  ];

  _module.args.buildInputs = baseBuildInputs;

  # lx-anonymizer-src = pkgs.fetchGit {
  #   url = "https://github.com/wg-lux/lx-anonymizer";
  #   ref = "prototype";
  #   # If you know the specific revision, it's better to use rev for reproducibility
  #   # rev = "abcdef1234567890"; 
  # };

  # imports = [ 
  #   "${lx-anonymizer-src}/devenv.nix"
  # ]; 

in 
{

  # A dotenv file was found, while dotenv integration is currently not enabled.
  dotenv.enable = true;
  dotenv.disableHint = true;

  packages = runtimePackages ++ buildInputs;

  env = {
    # include runtimePackages as well so runtime native libs (e.g. zlib) are on LD_LIBRARY_PATH
    LD_LIBRARY_PATH = lib.makeLibraryPath (buildInputs ++ runtimePackages) + ":/run/opengl-driver/lib:/run/opengl-driver-32/lib";
    # Force uv to use the Nix-provided Python - override any conflicts
    # UV_PYTHON = lib.mkForce "${python}/bin/python";
    # UV_PYTHON_DOWNLOADS = "never";
  };

  languages.python = {
    enable = true;
    package = python;
    uv = {
      enable = true;
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
    # Ensure runtimePackages are included in the library path here too
    export LD_LIBRARY_PATH="${
      with pkgs;
      lib.makeLibraryPath (buildInputs ++ runtimePackages)
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

    # if [ -d "$LX_ANONYMIZER_DIR" ]; then
    #   echo "lx-anonymizer directory exists. Pulling latest changes from $LX_ANONYMIZER_BRANCH..."
    #   (cd "$LX_ANONYMIZER_DIR" && git fetch origin && git checkout "$LX_ANONYMIZER_BRANCH" && git reset --hard "origin/$LX_ANONYMIZER_BRANCH")
    # else
    #   echo "lx-anonymizer directory does not exist. Cloning repository..."
    #   git clone -b "$LX_ANONYMIZER_BRANCH" "$LX_ANONYMIZER_REPO" "$LX_ANONYMIZER_DIR"
    # fi

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

    env-setup
  '';

  enterTest = ''
    nvcc -V
  '';
}
