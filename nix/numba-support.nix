{
  pkgs,
  lib,
}:
pkgs.symlinkJoin {
  name = "numba-native-deps";
  paths = [
    (lib.getLib pkgs.onetbb)
    (lib.getDev pkgs.onetbb)
    (lib.getLib pkgs.cython)
  ];
}
