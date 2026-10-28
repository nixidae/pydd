# nixpkgs in the planner (A) against stdenv as data (B): for each source node
# of `render-oracle.nix`, both must give one `.drv`. Both go through
# `lib/modes/nix-eval.nix`, so the template's context crosses JSON as it
# does into a planner.
{ nixpkgs }:
let
  oracle = import ./render-oracle.nix;
  pkgs = import nixpkgs { system = "x86_64-linux"; };
  template = (import ../lib/python/stdenv-template.nix { inherit pkgs; }).encoded;
  json =
    name: value: builtins.toFile name (builtins.unsafeDiscardStringContext (builtins.toJSON value));
  plain = json "document.json" oracle.document;
  carrying = json "document-stdenv.json" (oracle.document // { stdenv = template; });
  drvOf =
    document: extra: name:
    (import ../lib/modes/nix-eval.nix (
      {
        inherit document;
        graph = document': import ../lib/python/graph.nix (document' // { root = name; });
      }
      // extra
    )).drvPath;
  sources = builtins.filter (
    node:
    builtins.elem node.kind [
      "editable"
      "local-wheel"
      "local-sdist"
      "sdist-build"
    ]
  ) oracle.document.nodes;
in
builtins.listToAttrs (
  map (node: {
    inherit (node) name;
    value =
      let
        a = drvOf plain { nixpkgs = toString nixpkgs; } node.name;
        b = drvOf carrying { } node.name;
      in
      {
        inherit a b;
        same = a == b;
      };
  }) sources
)
