# One package per Nix version: `nix-store` is that version's libstore, as
# `nixVersions.<v>.libs.nix-store`. pkg-config carries its `-std=c++23` and
# its `Requires` chain.
{
  lib,
  stdenv,
  meson,
  ninja,
  pkg-config,
  nlohmann_json,
  nix-store,
}:
stdenv.mkDerivation {
  pname = "pydd-drv";
  inherit (nix-store) version;
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./main.cc
      ./meson.build
    ];
  };
  nativeBuildInputs = [
    meson
    ninja
    pkg-config
  ];
  buildInputs = [
    nix-store
    nlohmann_json
  ];
  meta.mainProgram = "pydd-drv";
}
