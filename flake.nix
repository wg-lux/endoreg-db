{
  description = "Application packaged using poetry2nix";
  inputs = {
    # flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

  };
  outputs = { self, nixpkgs, flake-utils, poetry2nix }:
    # flake-utils.lib.eachDefaultSystem (system:
    let
        system = "x86_64-linux";
        # see https://github.com/nix-community/poetry2nix/tree/master#api for more functions and examples.
        pkgs = nixpkgs.legacyPackages.${system};
        
        _poetry2nix = poetry2nix.lib.mkPoetry2Nix { inherit pkgs; };

    in
        {

          # Call with nix develop
          devShell."${system}" = pkgs.mkShell {
            buildInputs = [ 
              # env
              pkgs.poetry

              # Make venv (not very nixy but easy workaround to use current non-nix-packaged python module)
              pkgs.python3Packages.venvShellHook
            ];

            # Define Environment Variables
            TEST_VAR = "test";

            venvDir = ".venv";
            postShellHook = ''
              mkdir -p data

              pip install --upgrade pip
              poetry update

            '';
          };


        # });
        };
}
