{
  description = "Pure Nix packaging for endoreg-db";

  nixConfig = {
    extra-substituters = [ "https://cache.nixos-cuda.org" ];
    extra-trusted-public-keys = [
      "cuda-maintainers.cachix.org-1:0dq3bujKpuEPMCX6U4WylrUDZ9JyUG0VpVZa7CNfq5E="
    ];
  };

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
                    numba = pkgs.python312Packages.numba;
                    llvmlite = pkgs.python312Packages.llvmlite;
                  }
                )
              ]
            );

        resolvedUvDeps = pythonSet.resolveVirtualEnv workspace.deps.default;

        base = pkgs.callPackage ./package.nix {
          inherit pkgs;
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
