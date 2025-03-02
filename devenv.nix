{ pkgs, lib, config, inputs, ... }:
let
  buildInputs = with pkgs; [
    stdenv.cc.cc
    tesseract
    glib
    openssh
  ];

in 
{

  # A dotenv file was found, while dotenv integration is currently not enabled.
  dotenv.enable = false;
  dotenv.disableHint = true;

  packages = with pkgs; [
    cudaPackages.cuda_nvcc
    stdenv.cc.cc
    tesseract
  ];

  env = {
    LD_LIBRARY_PATH = "${
      with pkgs;
      lib.makeLibraryPath buildInputs
    }:/run/opengl-driver/lib:/run/opengl-driver-32/lib";
  };

  languages.python = {
    enable = true;
    uv = {
      enable = true;
      sync.enable = true;
    };
  };

  scripts = {
    hello.package = pkgs.zsh;
    hello.exec = "${pkgs.uv}/bin/uv run python hello.py";
    runtests.package = pkgs.zsh;
    runtests.exec = "${pkgs.uv}/bin/uv run python runtests.py";
  };

  tasks = {
  
  };

  processes = {
  };

  enterShell = ''
    . .devenv/state/venv/bin/activate
    runtests
    hello
  '';

  enterTest = ''
    nvcc -V
  '';
}
