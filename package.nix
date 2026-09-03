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
}:

let
  py = python312Packages;
  pname = "endoreg-db";
  version = "0.9.4.5";

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
    py.pyyaml
  ];


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
    cat > "$out/${py.python.sitePackages}/wsgi.py" <<'PY'
    """WSGI entry point for the packaged EndoReg DB application."""
    import os
    from django.core.wsgi import get_wsgi_application

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "endoreg_db.config.settings.prod")
    application = get_wsgi_application()
    PY
    export WG_LUX_FEATURE_SOURCE=${./feature-tracking}
    export WG_LUX_FEATURE_OUTPUT="$out/share/endoreg-db/features"
    ${py.python.interpreter} - <<'PY'
    import os
    from pathlib import Path
    import yaml

    source = Path(os.environ["WG_LUX_FEATURE_SOURCE"])
    output = Path(os.environ["WG_LUX_FEATURE_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    seen = set()
    top_keys = (
        "schema_version", "id", "name", "description", "owners",
        "production_critical", "source_documents", "invariants",
    )
    requirement_keys = (
        "id", "category", "title", "acceptance", "required", "verification",
    )
    for path in sorted(source.rglob("*.yml")):
        if path.name in {"policy.yml", "schema.example.yml", "standard.yml"}:
            continue
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            continue
        feature_id = value["id"]
        if feature_id in seen:
            raise ValueError(f"duplicate feature id: {feature_id}")
        seen.add(feature_id)
        specification = {key: value[key] for key in top_keys if key in value}
        specification["definition_of_done"] = [
            {key: requirement[key] for key in requirement_keys if key in requirement}
            for requirement in value.get("definition_of_done", [])
        ]
        (output / f"{feature_id}.yml").write_text(
            yaml.safe_dump(specification, sort_keys=False), encoding="utf-8"
        )
    PY
  '';

  meta = with lib; {
    description = "EndoReg DB Django app with native Rust extension";
    homepage = "https://github.com/wg-lux/endoreg-db";
    license = licenses.mit;
    platforms = platforms.linux;
  };
}
