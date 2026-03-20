{
  cargo,
  rustc,
  pkg-config,
  rustPlatform,
  lib,
  python312Packages,
  ffmpeg-headless,
  tesseract,
  ollama,
  pythonDeps,
}:

let
  py = python312Packages;
  pname = "endoreg-db";
  version = "0.9.1.6";

  tesseractWithLangs = tesseract.override {
    enableLanguages = [
      "deu"
      "eng"
    ];
  };

  src = lib.cleanSourceWith {
    src = ./.;
    filter =
      path: type:
      let
        rel = lib.removePrefix "${toString ./.}/" (toString path);
        base = builtins.baseNameOf (toString path);
        ignoredBaseNames = [
          ".devenv"
          ".direnv"
          ".env"
          ".git"
          ".mypy_cache"
          ".pytest_cache"
          ".ruff_cache"
          ".venv"
          "__pycache__"
          "htmlcov"
          "result"
          "target"
        ];
        ignoredPrefixes = [
          "data/tests/"
          "logs/"
          "media/"
          "staticfiles/"
          "storage/"
          "temp/"
        ];
      in
      !(builtins.elem base ignoredBaseNames || lib.any (prefix: lib.hasPrefix prefix rel) ignoredPrefixes);
  };
in
py.buildPythonApplication {
  inherit pname version src;
  pyproject = true;
  pythonRelaxDeps = true;
  dontCheckRuntimeDeps = true;

  cargoDeps = rustPlatform.importCargoLock {
    lockFile = ./rust/endoreg_rust_backend/Cargo.lock;
  };

  nativeBuildInputs = [
    rustPlatform.cargoSetupHook
    rustPlatform.maturinBuildHook
    cargo
    rustc
    pkg-config
  ];

  propagatedBuildInputs = pythonDeps;

  buildInputs = [
    ffmpeg-headless
    tesseractWithLangs
    ollama
  ];

  pythonImportsCheck = [
    "endoreg_db"
  ];

  # Keep the legacy WSGI entry module importable for the flake app launcher.
  postInstall = ''
    cp ${./wsgi.py} "$out/${py.python.sitePackages}/wsgi.py"
  '';

  meta = with lib; {
    description = "EndoReg DB Django app with native Rust extension";
    homepage = "https://github.com/wg-lux/endoreg-db";
    license = licenses.mit;
    platforms = platforms.linux;
  };
}
