{
  description = "Application packaged using poetry2nix";
  inputs = {
    # flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, poetry2nix }:
    let
        system = "x86_64-linux";
        pkgs = nixpkgs.legacyPackages.${system};
        _poetry2nix = poetry2nix.lib.mkPoetry2Nix { inherit pkgs; };

    in
        {
          # Call with nix develop
          devShell."${system}" = pkgs.mkShell {
            buildInputs = [ 
              pkgs.poetry
              pkgs.tesseract

              # Make venv (not very nixy but easy workaround to use current non-nix-packaged python module)
              pkgs.python3Packages.venvShellHook
            ];

            # Define Python venv
            venvDir = ".venv";
            postShellHook = ''
              mkdir -p data

              # pip install --upgrade pip
              # poetry update

            '';
          };


        # });
        };
}




# {
#   description = "Application packaged using poetry2nix";
#   inputs = {
#     # flake-utils.url = "github:numtide/flake-utils";
#     nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.11";
#     poetry2nix = {
#       url = "github:nix-community/poetry2nix";
#       inputs.nixpkgs.follows = "nixpkgs";
#     };

#   };
#   outputs = { self, nixpkgs, flake-utils, poetry2nix }:
#     # flake-utils.lib.eachDefaultSystem (system:
#     let
#         system = "x86_64-linux";
#         # see https://github.com/nix-community/poetry2nix/tree/master#api for more functions and examples.
#         pkgs = nixpkgs.legacyPackages.${system};
        
#         _poetry2nix = poetry2nix.lib.mkPoetry2Nix { inherit pkgs; };

#     in
#         {

#           # Call with nix develop
#           devShell."${system}" = pkgs.mkShell {
#             buildInputs = [ 
#               pkgs.python311
#               pkgs.python311.pkgs.requests
#               pkgs.python311.pkgs.pip
#               pkgs.python311.pkgs.virtualenv
#               # Make venv (not very nixy but easy workaround to use current non-nix-packaged python module)
#               pkgs.python3Packages.venvShellHook
#             ];

#             # Define Environment Variables
#             TEST_VAR = "test";

#             shellHook = ''
            # Tells pip to put packages into $PIP_PREFIX instead of the usual locations.
            # See https://pip.pypa.io/en/stable/user_guide/#environment-variables.
            # export PIP_PREFIX=$(pwd)/_build/pip_packages
            # export PYTHONPATH="$PIP_PREFIX/${pkgs.python3.sitePackages}:$PYTHONPATH"
            # export PATH="$PIP_PREFIX/bin:$PATH"
            # unset SOURCE_DATE_EPOCH
            
            # mkdir -p data

            # source .venv/bin/activate
#             '';

#             # venvDir = ".venv";
#             postShellHook = ''
#             '';
#           };


#         # });
#         };
# }
