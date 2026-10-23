/**
  The pydd library, with `writer` and `protocol` as the defaults for every
  planner, so an example is written once for every mode.

  The arguments are `plannerProtocol` and `plannerRender`, because
  `callPackage` would fill `protocol` from nixpkgs.

  `plannerRender` chooses how a Python environment turns its plan into
  derivations: `"nix"` evaluates `python/graph.nix` in huggorm,
  `"nix-cli"` evaluates it with `nix eval` and `modes/nix-eval.nix`, which
  needs EnsurePath (recursive-nix), and `"python"` renders the same
  derivations in Python. `null` takes `"nix"`
  with the huggorm writer on `builder-rpc-v0`, and `"python"` otherwise.
*/
{
  callPackage,
  huggorm,
  nix,
  nixvenv,
  pyddDrv ? null,
  writer ? "cli",
  plannerProtocol ? "rpc",
  plannerRender ? null,
  # nixpkgs for a planner to import (`recursive-nixpkgs`), as `pkgs.path`,
  # with overlays as files and config as JSON. `lib/modes/nix-eval.nix`
  # says why an overlay has to be a file.
  plannerNixpkgs ? null,
  plannerNixpkgsOverlays ? [ ],
  plannerNixpkgsConfig ? { },
  # A package set whose stdenv the planners get as data
  # (`python/stdenv-template.nix`), or `null`. Works in every mode.
  plannerStdenv ? null,
}:
let
  planner = callPackage ./core/planner.nix { inherit nixvenv; };
  self = planner // {
    inherit writer pyddDrv;
    nixpkgs = plannerNixpkgs;
    nixpkgsOverlays = plannerNixpkgsOverlays;
    nixpkgsConfig = plannerNixpkgsConfig;
    stdenv = plannerStdenv;
    protocol = plannerProtocol;
    render =
      if plannerRender != null then
        plannerRender
      else if writer == "huggorm" && plannerProtocol == "rpc" then
        "nix"
      else
        "python";
    mkPlanner =
      args:
      planner.mkPlanner (
        {
          inherit
            writer
            huggorm
            nix
            pyddDrv
            ;
          protocol = plannerProtocol;
        }
        // args
      );
    python = callPackage ./python { pydd = self; };
    rust = callPackage ./rust { pydd = self; };
    node = callPackage ./node { pydd = self; };
    evaluate = callPackage ./modes/evaluate.nix { pydd = self; };
  };
in
self
