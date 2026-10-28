# A planner with no Python and no pydd: a shell script that prints a graph as
# JSON and pipes it to `pydd-drv graph` (nixidae/nixidae#782). This is the shape
# a planner in any language takes.
#
# On `builder-rpc-v0` the planner writes two nodes, and the root reads the
# leaf through `{{leaf^out}}`. On released Nix a planner writes only its root,
# so the host makes the leaf and the root names it by path.
{
  pydd,
  bash,
  coreutils,
  lib,
}:
let
  inherit (bash.stdenv.hostPlatform) system;
  released = pydd.protocol == "out";
  # recursive-nix on a release: the nodes go through the build's daemon
  # socket, as on `builder-rpc-v0`, and the root to `$out`.
  recursive = pydd.protocol == "recursive";
  sh = "${bash}/bin/sh";

  hostLeaf = derivation {
    name = "graph-leaf";
    inherit system;
    builder = sh;
    args = [
      "-c"
      ''echo "hello from a node the host made" > $out''
    ];
  };

  node = name: args: inputs: {
    version = 4;
    inherit name system;
    builder = sh;
    inherit args;
    env = {
      inherit name system;
      builder = sh;
    };
    inputs = {
      srcs = [ "${bash}" ];
      drvs = inputs;
    };
    outputs.out = {
      method = "nar";
      hashAlgo = "sha256";
    };
  };

  leaf = node "graph-leaf" [ "-c" ''echo "hello from a node the planner wrote" > $out'' ] { };
  read = path: [
    "-c"
    ''read -r line < ${path}; echo "root read: $line" > $out''
  ];

  graph =
    if released then
      {
        root = "root";
        derivations.root = node "graph" (read (builtins.unsafeDiscardStringContext hostLeaf.outPath)) {
          ${builtins.unsafeDiscardStringContext hostLeaf.drvPath}.outputs = [ "out" ];
        };
      }
    else
      {
        root = "root";
        derivations = {
          inherit leaf;
          root = node "graph" (read "{{leaf^out}}") { leaf.outputs = [ "out" ]; };
        };
      };
  planner = derivation {
    # Nix requires the planner to carry the name of what it submits.
    name = "graph.drv";
    inherit system;
    builder = "${bash}/bin/bash";
    args = [
      "-c"
      (
        ''printf '%s' "$graph" | pydd-drv graph ''
        + (if recursive then ''--store "$NIX_REMOTE" --out'' else lib.optionalString released "--out")
      )
    ];
    graph = builtins.toJSON graph;
    # An attribute name carries no context, so the leaf's `.drv` becomes an
    # input of the planner here, and its output stays unbuilt.
    leafDrv = lib.optionalString released (builtins.unsafeDiscardOutputDependency hostLeaf.drvPath);
    PATH = lib.makeBinPath [
      pydd.pyddDrv
      coreutils
    ];
    requiredSystemFeatures =
      if recursive then [ "recursive-nix" ] else lib.optional (!released) "builder-rpc-v0";
    __contentAddressed = true;
    outputHashMode = "text";
    outputHashAlgo = "sha256";
  };
in
{
  inherit planner;
  out = builtins.outputOf planner.outPath "out";
  closure = pydd.pyddDrv;
}
