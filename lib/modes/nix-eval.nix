# A plan document, turned into derivations by `graph.nix`, evaluated by a
# stock `nix eval` inside the planner.
#
# **This is what EnsurePath buys.** `pydd.modes.evaluate._document` attaches
# the string context from Python, because `builder-rpc-v0` refuses
# `builtins.storePath` and `builtins.appendContext`. recursive-nix allows
# both, and Lillecarl/nix#371 allows them in `builder-rpc-v0`. With them the
# Nix language attaches the context itself, and the planner needs no huggorm.
#
# Neither call builds anything here. `storePath` names a path that is an
# input of the planner, so it is present. `appendContext` names a `.drv`
# that is an input, and an output of it that nothing realises.
{
  # The plan document, as a JSON file.
  document,
  # `lib/python/graph.nix`, or a function of the document as it is.
  graph,
  # nixpkgs as a store path, for `recursive-nixpkgs`, or "". The planner
  # imports it, and `graph.nix` builds a source node with its stdenv.
  nixpkgs ? "",
  # **An overlay is a function, and no function crosses into a sandbox.** So
  # the overlays are files, as a JSON list of store paths, and `config` is
  # JSON. An overlay that exists only in the caller's evaluation is lost.
  overlays ? "[]",
  config ? "{}",
}:
let
  inherit (builtins) storeDir;

  # A key whose value is an output of the `.drv` that the paired key names:
  # `pydd.python._plan.BUILT_PAIRS`.
  pairs = {
    out = "drv";
    plannerOut = "plannerDrv";
  };
  outputName = entry: key: if key == "out" then entry.output or "out" else "out";

  inStore = text: builtins.substring 0 (builtins.stringLength storeDir + 1) text == "${storeDir}/";
  # `/nix/store/<hash>-<name>/sub` is `/nix/store/<hash>-<name>`, and `/sub`.
  split =
    text:
    let
      rest = builtins.substring (builtins.stringLength storeDir + 1) (-1) text;
      parts = builtins.match "([^/]+)(.*)" rest;
    in
    {
      top = "${storeDir}/${builtins.elemAt parts 0}";
      sub = builtins.elemAt parts 1;
    };

  value =
    data:
    # A string that carries its context, as `builtins.getContext` gives it.
    if builtins.isAttrs data && data ? __context then
      builtins.appendContext data.text data.__context
    else if builtins.isAttrs data then
      builtins.mapAttrs (
        name: item:
        let
          drv = if pairs ? ${name} then data.${pairs.${name}} or null else null;
        in
        if builtins.isString drv && builtins.isString item then
          builtins.appendContext item { ${drv}.outputs = [ (outputName data name) ]; }
        else if builtins.elem name (builtins.attrValues pairs) then
          item
        else
          value item
      ) data
    else if builtins.isList data then
      map value data
    else if builtins.isString data && inStore data then
      let
        parts = split data;
      in
      "${builtins.storePath parts.top}${parts.sub}"
    else
      data;

  plan = value (builtins.fromJSON (builtins.readFile document));
  pkgs = import nixpkgs {
    inherit (plan) system;
    overlays = map import (builtins.fromJSON overlays);
    config = builtins.fromJSON config;
  };
in
(if builtins.isFunction graph then graph else import graph) (
  plan // (if nixpkgs == "" then { } else { inherit pkgs; })
)
