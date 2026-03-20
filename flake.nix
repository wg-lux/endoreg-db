{
  description = "Pure Nix packaging for endoreg-db";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";

    uv2nix.url = "github:pyproject-nix/uv2nix";
    pyproject-nix.url = "github:pyproject-nix/pyproject.nix";
    pyproject-build-systems.url = "github:pyproject-nix/build-system-pkgs";
  };

  outputs =
    inputs@{
      self,
      nixpkgs,
      flake-utils,
      uv2nix,
      pyproject-nix,
      pyproject-build-systems,
      ...
    }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
    in
    flake-utils.lib.eachSystem systems (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        workspace = uv2nix.lib.workspace.loadWorkspace {
          workspaceRoot = ./.;
        };

        uvOverlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel";
        };

        pythonSet =
          (pkgs.callPackage pyproject-nix.build.packages {
            python = pkgs.python312;
          }).overrideScope
            (
              pkgs.lib.composeManyExtensions [
                pyproject-build-systems.overlays.wheel
                uvOverlay
                (
                  final: prev: {
                    numba = prev.numba.overrideAttrs (old: {
                      buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.onetbb ];
                    });
                  }
                )
              ]
            );

        resolvedUvDeps = pythonSet.resolveVirtualEnv workspace.deps.default;

        pythonDeps =
          builtins.filter
            (
              drv:
              let
                depName = drv.pname or (pkgs.lib.getName drv);
              in
              depName != "endoreg-db" && depName != "endoreg_db"
            )
            resolvedUvDeps;

        base = pkgs.callPackage ./package.nix {
          inherit pythonDeps;
        };
        server_env = pkgs.python312.withPackages (_: [ base ]);
        server = pkgs.writeShellApplication {
          name = "endoreg-db-server";
          runtimeInputs = [ server_env ];
          text = ''
            export DJANGO_SETTINGS_MODULE="''${DJANGO_SETTINGS_MODULE:-endoreg_db.config.settings.prod}"
            exec python -m gunicorn --bind "''${DJANGO_HOST:-0.0.0.0}:''${DJANGO_PORT:-8188}" wsgi:application
          '';
        };
      in
      {
        packages = {
          default = base;
          endoreg-db = base;
          endoreg-db-with-native = base;
        };

        apps.default = {
          type = "app";
          program = "${server}/bin/endoreg-db-server";
        };

        checks = {
          endoreg-db = base;
        };
      }
    );
}
