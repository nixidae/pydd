# A wrapper: every output is an attribute of `default.nix`. Work on pydd
# happens in nixidae, which this repository is exported from. `flake.lock`
# names the nixidae commit; lock-subtrees writes it.
{
  inputs.nixidae = {
    url = "github:nixidae/nixidae";
    flake = false;
  };

  outputs =
    { nixidae, ... }:
    let
      sources = import "${nixidae}/nix/wire.nix" { };
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems =
        f:
        builtins.listToAttrs (
          map (system: {
            name = system;
            value = f system;
          }) systems
        );
      each = forAllSystems (
        system:
        import ./. {
          inherit sources;
          huggormSet = import sources.huggorm {
            inherit sources;
            pkgs = import sources.nixpkgs {
              inherit system;
              config = { };
              overlays = [ ];
            };
          };
        }
      );
    in
    {
      packages = builtins.mapAttrs (_: p: {
        inherit (p.packages) default pydd-drv;
        pydd-docs = p.docs.default;
      }) each;
    };
}
