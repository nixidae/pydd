/**
  The artefacts of the registry packages of a `uv.lock`, as rows of the menu.

  `./build-systems` reads pydd's own lock with it, and `./script.nix` the lock
  that `uv lock --script` writes. Each row fetches one file by its url and hash.
*/
{
  pkgs,
  # The parsed lock file.
  document,
  # Extra keys for every row.
  extra ? { },
}:
let
  inherit (pkgs) lib;
  rowOf =
    package: kind: file:
    let
      filename = lib.last (lib.splitString "/" file.url);
    in
    {
      inherit kind filename;
      package = package.name;
      inherit (package) version;
      drv = pkgs.fetchurl {
        inherit (file) url hash;
        name = filename;
      };
    }
    // extra;
  rowsOf =
    package:
    map (rowOf package "wheel") (package.wheels or [ ])
    ++ map (rowOf package "sdist") (lib.optional (package ? sdist) package.sdist);
in
lib.concatMap rowsOf (builtins.filter (package: package.source ? registry) (document.package or [ ]))
