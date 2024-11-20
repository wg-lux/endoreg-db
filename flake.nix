{
  description = "Hello world flake using uv2nix";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:nix-community/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:adisbladis/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  # Note that uv2nix is _not_ using Nixpkgs buildPythonPackage.
  # It's using https://nix-community.github.io/pyproject.nix/build.html

  outputs =
    {
      self,
      nixpkgs,
      uv2nix,
      pyproject-nix,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      # Load a uv workspace from a workspace root.
      # Uv2nix treats all uv projects as workspace projects.
      # https://adisbladis.github.io/uv2nix/lib/workspace.html
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      # Create package overlay from workspace.
      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel"; # or sourcePreference = "sdist";
      };

      editableOverlay = workspace.mkEditablePyprojectOverlay {
        root = "$REPO_ROOT";
        # root="./.";
      };

      # Python sets grouped per system
      pythonSets = forAllSystems (
        system:
        let
          # pkgs = nixpkgs.legacyPackages.${system};
          pkgs = import nixpkgs { 
            system = "${system}";
            config.allowUnfree = true; 
          };
          inherit (pkgs) stdenv;
          interpreter = pkgs.python312;


          # Base Python package set from pyproject.nix
          baseSet = pkgs.callPackage pyproject-nix.build.packages {
            python = interpreter;
          };

          # An overlay of build fixups & test additions
          pyprojectOverrides = final: prev: {

            endoreg-db = prev.endoreg-db.overrideAttrs (old: {

              # Add tests to passthru.tests
              #
              # These attribute are used in Flake checks.
              passthru = old.passthru // {
                tests =
                  (old.tests or { })
                  // {

                    # Run mypy checks
                    mypy =
                      let
                        venv = final.mkVirtualEnv "endoreg-db-typing-env" {
                          endoreg-db = [ "typing" ];
                        };
                      in
                      stdenv.mkDerivation {
                        name = "${final.endoreg-db.name}-mypy";
                        inherit (final.endoreg-db) src;
                        nativeBuildInputs = [
                          venv
                        ];
                        dontConfigure = true;
                        dontInstall = true;
                        buildPhase = ''
                          mkdir $out
                          mypy --strict . --junit-xml $out/junit.xml
                        '';
                      };

                    # Run pytest with coverage reports installed into build output
                    pytest =
                      let
                        venv = final.mkVirtualEnv "endoreg-db-pytest-env" {
                          endoreg-db = [ "test" ];
                        };
                      in
                      stdenv.mkDerivation {
                        name = "${final.endoreg-db.name}-pytest";
                        inherit (final.endoreg-db) src;
                        nativeBuildInputs = [
                          venv
                        ];

                        dontConfigure = true;

                        buildPhase = ''
                          runHook preBuild
                          pytest --cov tests --cov-report html tests
                          runHook postBuild
                        '';

                        installPhase = ''
                          runHook preInstall
                          mv htmlcov $out
                          runHook postInstall
                        '';
                      };
                  };
              };
            });

            numpy = prev.numpy.overrideAttrs (old: {
              nativeBuildInputs =
                old.nativeBuildInputs or [ ]
                ++ (final.resolveBuildSystem {
                  cython = [ ];
                  meson-python = [ ];
                });
            });

          };

        in
        baseSet.overrideScope (lib.composeExtensions overlay pyprojectOverrides)
      );

    in
    {
      checks = forAllSystems (
        system:
        let
          pythonSet = pythonSets.${system};
        in
        # Inherit tests from passthru.tests into flake checks
        pythonSet.endoreg-db.passthru.tests
      );


      # Use an editable Python set for development.
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          editablePythonSet = pythonSets.${system}.overrideScope editableOverlay;
          venv = editablePythonSet.mkVirtualEnv "endoreg-db-dev-env" {
            endoreg-db = [ "dev" ];
          };
        in
        {
          default = pkgs.mkShell {
            packages = [
              venv
              pkgs.uv
            ];
            env.REPO_ROOT = "./.";

            shellHook = ''
              unset PYTHONPATH
              # export REPO_ROOT=$(git rev-parse --show-toplevel)
              export UV_NO_SYNC=1
            '';
          };
          impure = pkgs.mkShell {
            packages = [
              pkgs.python312
              pkgs.uv
            ];
            shellHook = ''
              unset PYTHONPATH
            '';
          };
        }
      );

    };
}
