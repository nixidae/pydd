# Two revisions of one real project, planned each: a package whose lock entry
# did not change must be the same derivation in both. Not in `checks`: it
# fetches whole projects. Issue #894.
#
#     nix run --file . run -- node-revisions.t3code
{ pkgs, pydd }:
let
  github =
    owner: repo: rev: hash:
    pkgs.fetchFromGitHub {
      inherit
        owner
        repo
        rev
        hash
        ;
    };
  pairs = {
    # The lock changes in apps/mobile only, which `members` leaves out, so no
    # package changes. node-pty, pierre-diffs and micromark-extension-gfm-table
    # carry pnpm patches: they rebuilt on every commit before #894.
    t3code = {
      before =
        github "pingdotgg" "t3code" "7e289ae1f77b9137580688aaccf00d48906984ad"
          "sha256-7fMSp2UUdwhy+ryWKO2rfBM+KA1Z8wZDuirVPtSkpaQ=";
      after =
        github "pingdotgg" "t3code" "ec585729e0a48c318f4cfb0c232b816c029d0541"
          "sha256-yPPK+mt8jcyH2X2IoGTG7Kiu3vHmW+5Ojd7gOPJiXos=";
      # As croshome's pkgs/t3code.nix builds it.
      arguments = {
        name = "t3code";
        program = "t3";
        nodejs = pkgs.nodejs_24;
        members = [
          "apps/server"
          "scripts"
        ];
        overrides.electron.env.ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
      };
    };
  };
in
builtins.mapAttrs (
  name: pair:
  let
    root = src: pydd.node.npm ({ project = src; } // pair.arguments);
    planned = src: (root src).planner;
  in
  {
    # What `run` copies into its store first.
    closure = pkgs.linkFarmFromDrvs "${name}-closure" [
      (root pair.before).closure
      (root pair.after).closure
    ];
    # Each planner writes its root `.drv`, which names every package node.
    # Only the planners run; no package builds.
    out = derivation {
      name = "${name}-revisions";
      inherit (pkgs.stdenv.hostPlatform) system;
      builder = "${pkgs.bash}/bin/bash";
      PATH = "${pkgs.coreutils}/bin:${pkgs.gnugrep}/bin:${pkgs.diffutils}/bin";
      before = planned pair.before;
      after = planned pair.after;
      args = [
        "-c"
        ''
          set -eu
          nodes() { grep -o '/nix/store/[a-z0-9]\{32\}-[^"]*\.drv' "$1" | sort -u; }
          nodes "$before" > before
          nodes "$after" > after
          if ! diff before after; then
            echo "${name}: the package nodes above differ between the two revisions" >&2
            exit 1
          fi
          echo "ok: $(wc -l < before) package nodes, the same in both" > $out
        ''
      ];
    };
  }
) pairs
