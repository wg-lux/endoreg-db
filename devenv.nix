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
    ffmpeg_6-headless
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
    runtests-media.exec = "${pkgs.uv}/bin/uv run python runtests.py 'media'";
    runtests-dataloader.exec = "${pkgs.uv}/bin/uv run python runtests.py 'dataloader'";
    runtests-other.exec = "${pkgs.uv}/bin/uv run python runtests.py 'other'";
    runtests-helpers.exec = "${pkgs.uv}/bin/uv run python runtests.py 'helpers'";
    runtests-administration.exec = "${pkgs.uv}/bin/uv run python runtests.py 'administration'";
    runtests-medical.exec = "${pkgs.uv}/bin/uv run python runtests.py 'medical'";
    pyshell.exec = "${pkgs.uv}/bin/uv run python manage.py shell";
  };

  tasks = {
  
  };

  processes = {
  };

  enterShell = ''
    . .devenv/state/venv/bin/activate
    runtests-dataloader
    hello
  '';

  enterTest = ''
    nvcc -V
  '';
}
