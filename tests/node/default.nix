# npm projects, each with a check that runs what it built.
#
#     nix run --file . run -- node.cli.hello-npm
{ pkgs, pydd }:
let
  projects = {
    # An ES module over chalk 4, which is CommonJS: ansi-styles 6 at the top
    # of the tree and ansi-styles 4 nested under chalk.
    hello-npm = {
      src = ./hello-npm;
      check = ''
        test "$(hello-npm)" = 'hello from hello-npm semver=7 styles=6/4'
      '';
    };
    # better-sqlite3's install script tries prebuild-install, which the
    # node refuses, and then node-gyp compiles the SQLite it bundles.
    native-npm = {
      src = ./native-npm;
      check = ''
        test "$(native-npm)" = 'hello from native-npm sqlite=3 answer=42'
      '';
    };
    # esbuild has a package for each platform, as optional dependencies, and
    # an install script that checks the one it gets. The root runs `npm run
    # build`, and the result holds no development dependency.
    esbuild-app = {
      src = ./esbuild-app;
      check = ''
        test "$(esbuild-app)" = 'hello from esbuild-app day=86400000'
        modules=$(dirname "$(readlink -f "$(command -v esbuild-app)")")/../node_modules
        test -d "$modules/ms"
        test ! -e "$modules/esbuild"
        test ! -e "$modules/@esbuild"
        test ! -e "$modules/.bin"
      '';
    };
    # An npm workspace: the program is a member's, and its dependency is the
    # other member, through the link that the lock names.
    workspace = {
      src = ./workspace;
      arguments.program = "workspace-app";
      check = ''
        test "$(workspace-app)" = 'hello from workspace-app hour=3600000'
      '';
    };
    # isarray from a commit on GitHub, which needs its hash, and ms from a
    # directory that the call gives in place of the registry's tarball.
    git-dep = {
      src = ./git-dep;
      arguments = {
        outputHashes."isarray-2.0.5" = "sha256-pGqpNBCt0CJ97Rvkg7XRNtCvByBNWTKXVle0zj4byZg=";
        overrides.ms.src = pkgs.runCommand "ms-patched" { } ''
          mkdir $out
          echo '{"name": "ms", "version": "2.1.3"}' > $out/package.json
          echo 'module.exports = () => "patched";' > $out/index.js
        '';
      };
      check = ''
        test "$(git-dep)" = 'hello from git-dep array=true ms=patched'
      '';
    };
    # hello-npm from pnpm's lock: a graph of versions, which the reader
    # places as npm does. The lock does not name semver's program, so the
    # root reads it from the package.
    pnpm-hello = {
      src = ./pnpm-hello;
      lock = "pnpm";
      check = ''
        test "$(hello-npm)" = 'hello from hello-npm semver=7 styles=6/4'
        modules=$(dirname "$(readlink -f "$(command -v hello-npm)")")/../node_modules
        test "$(readlink "$modules/.bin/semver")" = ../semver/bin/semver.js
      '';
    };
    # pnpm-hello with `pnpm patch` on semver: its major is ten times larger.
    pnpm-patch = {
      src = ./pnpm-patch;
      lock = "pnpm";
      check = ''
        test "$(hello-npm)" = 'hello from hello-npm semver=70 styles=6/4'
      '';
    };
    # The npm workspace with pnpm: a member links to the other member.
    pnpm-workspace = {
      src = ./pnpm-workspace;
      lock = "pnpm";
      arguments.program = "workspace-app";
      check = ''
        test "$(workspace-app)" = 'hello from workspace-app hour=3600000'
      '';
    };
    # better-sqlite3 from bun's lock. bun runs the install script of a
    # package that `trustedDependencies` names.
    bun-native = {
      src = ./bun-native;
      lock = "bun";
      check = ''
        test "$(native-npm)" = 'hello from native-npm sqlite=3 answer=42'
      '';
    };
    # esbuild from Yarn v1's lock, which names no platform: every package of
    # esbuild installs for the build, and the result omits all of them.
    yarn-esbuild = {
      src = ./yarn-esbuild;
      lock = "yarn";
      check = ''
        test "$(esbuild-app)" = 'hello from esbuild-app day=86400000'
        modules=$(dirname "$(readlink -f "$(command -v esbuild-app)")")/../node_modules
        test ! -e "$modules/esbuild"
        test ! -e "$modules/@esbuild"
      '';
    };
  };
in
builtins.mapAttrs (
  name: entry:
  let
    root = pydd.node.npm ({ project = entry.src; } // entry.arguments or { });
  in
  {
    out = derivation {
      name = "${name}-check";
      inherit (pkgs.stdenv.hostPlatform) system;
      builder = "${pkgs.bash}/bin/bash";
      PATH = "${root}/bin:${pkgs.coreutils}/bin";
      args = [
        "-c"
        ''
          set -eu
          ${entry.check}
          echo ok > $out
        ''
      ];
    };
    environment = root.dynamic;
    inherit root;
    inherit (root) closure;
  }
  # `out` reads npm's lock on the host, and no other.
) (pkgs.lib.filterAttrs (_: entry: pydd.protocol != "out" || !(entry ? lock)) projects)
