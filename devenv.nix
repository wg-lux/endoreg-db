{ pkgs, lib, config, inputs, baseBuildInputs, ... }:
let
  # --- Project Configuration ---
  DJANGO_MODULE = "endoreg_db";
  host = "localhost";
  port = "8188";

  confDir = "./conf";

  # Pin to specific Python 3.12 version to match pyproject.toml
  python = pkgs.python312; #known devenv issue with python3Packages since python3Full was deprecated
  uvPackage = pkgs.uv;
  
  numbaSupport = pkgs.callPackage ./nix/numba-support.nix { };

  buildInputs = with pkgs; [
    python312
    stdenv.cc.cc
    clang
    glib
    openssh
    cmake
    gcc
    pkg-config
    protobuf
    libglvnd
    libxcb
    libx11
    cargo
    rustc
    rustfmt
    maturin
    onetbb
    numbaSupport
  ];
  runtimePackages = with pkgs; [
    stdenv.cc.cc
    clang
    ffmpeg-headless.bin
    jq
    ripgrep
    tesseract
    uvPackage
    libglvnd # Add libglvnd for libGL.so.1
    glib
    zlib
    ollama.out
    tesseract
    # --- ADDED THESE FOR OPENCV 4.13+ SUPPORT ---
    libxcb      # Provides libxcb.so.1
    libx11      # Common dependency for XCB
    libxext     # Common dependency for OpenCV
    libxrender  # Common dependency for OpenCV
    libxkbcommon     # Often required by newer Qt/OpenCV builds
    # ------------------------------------------
    cargo
    rustc
    rustfmt
    maturin
    valgrind
    kdePackages.kcachegrind     # Contains both kcachegrind and the pure Qt qcachegrind
    graphviz        # Enables the call-graph visualization tab inside Cachegrind
    python312
    python312Packages.pyprof2calltree

  ];
  
  SYNC_CMD = "uv sync --extra dev --extra docs";
  FAST_TEST_MARKER = "not (expensive or video or pipeline or ai or slow or ffmpeg)";
  HEAVY_TEST_MARKER = "expensive or video or pipeline or ai or slow or ffmpeg";
  COVERAGE_ARGS = "--cov=./endoreg_db/models --cov=./endoreg_db/data --cov=./endoreg_db/factories --cov=./endoreg_db/serializers --cov=./endoreg_db/utils --cov=./endoreg_db/views --cov=endoreg_db.services.audit_integrity --cov=endoreg_db.tasks --cov-report=term:skip-covered";

  _module.args.buildInputs = baseBuildInputs;

  # this is an example of how to include packages devenv locally for development
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

  dotenv.enable = true;
  dotenv.disableHint = true;

  packages = runtimePackages ++ buildInputs;

  env = {
    # include runtimePackages as well so runtime native libs (e.g. zlib) are on LD_LIBRARY_PATH
    LD_LIBRARY_PATH = lib.makeLibraryPath (buildInputs ++ runtimePackages ++ [ pkgs.stdenv.cc.cc.lib ]) + ":/run/opengl-driver/lib:/run/opengl-driver-32/lib";
    PYO3_PYTHON = "${python}/bin/python";
    UV_PYTHON = lib.mkForce "${python}/bin/python";
    UV_PYTHON_DOWNLOADS = "never";
  };

  languages.python = {
    enable = true;
    version = "3.12";
    uv = {
      enable = true;
      package = uvPackage;
      sync.enable = false;
    };
  };

  languages.rust.enable = true;

  outputs =
    lib.optionalAttrs (inputs ? pyproject-nix ) (
      let
        workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
          workspaceRoot = ./.;
        };

        overlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel";
        };

        pythonSet =
          (pkgs.callPackage inputs.pyproject-nix.build.packages {
            python = pkgs.python312;
          }).overrideScope (lib.composeManyExtensions [
            inputs.pyproject-build-systems.overlays.default
            overlay
            (
              final: prev: {
                numba = prev.numba.overrideAttrs (old: {
                  buildInputs = (old.buildInputs or [ ]) ++ [
                    pkgs.onetbb
                    numbaSupport
                  ];
                  preFixup = (old.preFixup or "") + ''
                    addAutoPatchelfSearchPath ${lib.getLib pkgs.onetbb}/lib
                    addAutoPatchelfSearchPath ${numbaSupport}/lib
                  '';
                });
              }
            )
          ]);

        pythonApp = pythonSet.mkVirtualEnv "endoreg_db-env" workspace.deps.default;
        nativeDrv = pkgs.rustPlatform.buildRustPackage {
          pname = "rust_endoreg_rust_backend";
          version = "0.1.0";
          src = ./rust/endoreg_rust_backend;
          cargoLock.lockFile = ./rust/endoreg_rust_backend/Cargo.lock;
          cargoBuildFlags = [ "--lib" ];
          doCheck = false;
          nativeBuildInputs = [ python ];
          PYO3_PYTHON = "${python}/bin/python";
        };
        nativeLibDrv = lib.getLib nativeDrv;

        nativeApp = pkgs.runCommand "endoreg-rust-backend-0.1.0" { } ''
          mkdir -p "$out/${python.sitePackages}"
          native_lib="$(find -L ${nativeLibDrv}/lib -type f -name 'libendoreg_rust_backend*.so' | head -n 1)"
          test -n "$native_lib"
          cp "$native_lib" "$out/${python.sitePackages}/endoreg_rust_backend.so"
        '';
      in
      {
        python = pythonApp;
        native = nativeApp;
        native-raw = nativeDrv;
        app = pkgs.symlinkJoin {
          name = "endoreg-db-with-native";
          paths = [
            pythonApp
            nativeApp
          ];
        };
      }
    );

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
    # Ensure runtimePackages are included in the library path
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
        mkdocs.exec = ''
      uv run make -C docs html
      uv run make -C docs linkcheck
    '';
    uvsnc.exec = ''
      sync_cmd="${SYNC_CMD}"
      $sync_cmd
    '';
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
        rm -rf .devenv/
        echo "Removing uv lock file: uv.lock"
        rm -f uv.lock
        direnv allow
        uv sync
      '';
    };
    "agent:sync" = {
      description = "Sync the Python environment for Codex/agent workflows";
      exec = ''
        sync_cmd="${SYNC_CMD}"
        $sync_cmd
      '';
    };
    "agent:format" = {
      description = "Run the mutating format/lint hooks used after agent edits";
      exec = ''
        .devenv/state/venv/bin/pre-commit run ruff --all-files
        .devenv/state/venv/bin/pre-commit run ruff-format --all-files
      '';
    };
    "agent:smoke" = {
      description = "Run quick import and deployment-contract checks after scoped edits";
      exec = ''

        .devenv/state/venv/bin/python scripts/check_django_startup_imports.py
        .devenv/state/venv/bin/pytest tests/deployment/test_prod_settings_contract.py -q
      '';
    };
    "rust:stubs" = {
      description = "Regenerate Python stubs for the PyO3 Rust backend";
      exec = ''
        cargo run --manifest-path rust/endoreg_rust_backend/Cargo.toml --bin stub_gen
        cp rust/endoreg_rust_backend/endoreg_rust_backend.pyi endoreg_db/endoreg_rust_backend.pyi
        rm rust/endoreg_rust_backend/endoreg_rust_backend.pyi
      '';
    };
    "rust:report-runtime" = {
      description = "Verify Rust report snapshot tests, stubs, capability, and wheel";
      exec = "scripts/check_report_native_runtime.sh";
    };
    "agent:pre-commit" = {
      description = "Run the full default pre-commit suite for agent preflight";
      exec = ".devenv/state/venv/bin/pre-commit run --all-files";
    };
    "quality:dead-code" = {
      description = "Reject new, stale, or expired reviewed dead-code findings";
      exec = ".devenv/state/venv/bin/python scripts/check_dead_code.py";
    };
    "quality:boundaries" = {
      description = "Reject unreviewed broad exceptions and type suppressions";
      exec = ".devenv/state/venv/bin/python scripts/check_quality_boundaries.py";
    };
    "quality:type-safety-operational" = {
      description = "Rehearse the persisted DICOM V2 JSON migration path";
      exec = ".devenv/state/venv/bin/pytest tests/services/test_dicom_manifest_backfill.py -q";
    };
    "quality:code-regression" = {
      description = "Run the versioned quality guards and fast regression lane";
      exec = ''
        .devenv/state/venv/bin/pyright
        .devenv/state/venv/bin/python scripts/check_dead_code.py
        .devenv/state/venv/bin/python scripts/check_quality_boundaries.py
        devenv tasks run test:fast
      '';
    };
    "celery:check" = {
      description = "Validate Celery broker, queue, and secure transport settings";
      exec = ''
        .devenv/state/venv/bin/python manage.py check --tag celery
      '';
    };
    "celery:worker:pipeline" = {
      description = "Run a Celery worker for pipeline ingest jobs";
      exec = ''
        devenv tasks run celery:check
        queue=''${CELERY_PIPELINE_QUEUE:-pipeline}
        concurrency=''${CELERY_PIPELINE_CONCURRENCY:-2}
        .devenv/state/venv/bin/celery -A endoreg_db worker --loglevel=''${CELERY_LOGLEVEL:-INFO} -Q "$queue" -n "endoreg-pipeline@%h" --concurrency="$concurrency" --prefetch-multiplier=1
      '';
    };
    "celery:worker:ffmpeg" = {
      description = "Run a Celery worker for video import/reimport and FFmpeg media jobs";
      exec = ''
        devenv tasks run celery:check
        queue=''${CELERY_FFMPEG_MEDIA_QUEUE:-ffmpeg_media}
        concurrency=''${CELERY_FFMPEG_MEDIA_CONCURRENCY:-1}
        .devenv/state/venv/bin/celery -A endoreg_db worker --loglevel=''${CELERY_LOGLEVEL:-INFO} -Q "$queue" -n "endoreg-ffmpeg@%h" --concurrency="$concurrency" --prefetch-multiplier=1
      '';
    };
    "celery:worker:frames" = {
      description = "Run a Celery worker for frame extraction jobs";
      exec = ''
        devenv tasks run celery:check
        queue=''${CELERY_FRAME_EXTRACTION_QUEUE:-frame_extraction}
        concurrency=''${CELERY_FRAME_EXTRACTION_CONCURRENCY:-2}
        .devenv/state/venv/bin/celery -A endoreg_db worker --loglevel=''${CELERY_LOGLEVEL:-INFO} -Q "$queue" -n "endoreg-frames@%h" --concurrency="$concurrency" --prefetch-multiplier=1
      '';
    };
    "celery:worker:inference" = {
      description = "Run a Celery worker for vision and LLM inference jobs";
      exec = ''
        devenv tasks run celery:check
        queues=''${CELERY_INFERENCE_QUEUE:-inference},''${CELERY_LLM_INFERENCE_QUEUE:-llm_inference}
        concurrency=''${CELERY_INFERENCE_CONCURRENCY:-1}
        .devenv/state/venv/bin/celery -A endoreg_db worker --loglevel=''${CELERY_LOGLEVEL:-INFO} -Q "$queues" -n "endoreg-inference@%h" --concurrency="$concurrency" --prefetch-multiplier=1
      '';
    };
    "celery:worker:training" = {
      description = "Run a Celery worker for model training jobs";
      exec = ''
        devenv tasks run celery:check
        queue=''${CELERY_TRAINING_QUEUE:-model_training}
        concurrency=''${CELERY_TRAINING_CONCURRENCY:-1}
        .devenv/state/venv/bin/celery -A endoreg_db worker --loglevel=''${CELERY_LOGLEVEL:-INFO} -Q "$queue" -n "endoreg-training@%h" --concurrency="$concurrency" --prefetch-multiplier=1
      '';
    };
    "celery:worker:maintenance" = {
      description = "Run a Celery worker for maintenance and audit jobs";
      exec = ''
        devenv tasks run celery:check
        queue=''${CELERY_MAINTENANCE_QUEUE:-maintenance}
        concurrency=''${CELERY_MAINTENANCE_CONCURRENCY:-1}
        .devenv/state/venv/bin/celery -A endoreg_db worker --loglevel=''${CELERY_LOGLEVEL:-INFO} -Q "$queue" -n "endoreg-maintenance@%h" --concurrency="$concurrency" --prefetch-multiplier=1
      '';
    };
    "celery:beat" = {
      description = "Run Celery beat for scheduled maintenance jobs";
      exec = ''
        devenv tasks run celery:check
        .devenv/state/venv/bin/celery -A endoreg_db beat --loglevel=''${CELERY_LOGLEVEL:-INFO}
      '';
    };
    "test:sync" = {
      description = "Ensure pytest";
      exec = "uv sync --extra dev";
    };
    "test:fast" = {
      description = "Run the fast PR pytest lane with live logging";
      exec = ''
        devenv tasks run test:sync
        export SKIP_EXPENSIVE_TESTS=true
        export RUN_VIDEO_TESTS=false
        export USE_STUB_MODEL_META=true
        export TEST_DB_REUSE=true
        pytest -s -o log_cli=true --log-level=INFO -m '${FAST_TEST_MARKER}' -n auto --dist=loadscope
      '';
    };
    "test:heavy" = {
      description = "Run heavy tests with live logging";
      exec = ''
        devenv tasks run test:sync
        export SKIP_EXPENSIVE_TESTS=false
        export RUN_VIDEO_TESTS=true
        export USE_STUB_MODEL_META=true
        export TEST_DB_REUSE=true
        pytest -s -o log_cli=true --log-level=INFO -m '${HEAVY_TEST_MARKER}' -n auto --dist=loadscope
      '';
    };
    "test:full" = {
      description = "Run the full pytest suite with live logging";
      exec = ''
        devenv tasks run test:sync
        export SKIP_EXPENSIVE_TESTS=false
        export RUN_VIDEO_TESTS=true
        export USE_STUB_MODEL_META=true
        export TEST_DB_REUSE=true
        pytest -s -o log_cli=true --log-level=INFO -n auto --dist=loadscope ${COVERAGE_ARGS}
      '';
    };
    "test:clean" = {
      description = "Remove pytest worker runtimes, temp directories, and test SQLite files";
      exec = ''
        devenv tasks run test:sync
        python - <<'PY'
        from pathlib import Path

        from endoreg_db.utils.filesystem.file_operations import safe_rmtree, safe_unlink_file

        root = Path("data/tests")
        for path in (root / "workers", root / "tmp"):
            safe_rmtree(path, missing_ok=True)

        db_root = root / "db"
        for pattern in ("test_db*.sqlite3", "test_db*.sqlite3-wal", "test_db*.sqlite3-shm"):
            for path in db_root.glob(pattern):
                safe_unlink_file(path, missing_ok=True)
        PY
      '';
    };
  
  };

  processes = {

  };

  enterShell = ''

    # Add the uv virtual environment directly to your PATH
    export PATH="$PWD/.devenv/state/venv/bin:$PATH"
    
  '';

  enterTest = ''
    nvcc -V
  '';
}
