# Real npm projects, pinned to the releases that nixpkgs builds: live practice
# for what an npm project in the wild needs. Each entry says what, if
# anything, it needs beyond the project itself.
#
#     nix run --file . run -- node-live.cli.nodemon
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
  projects = {
    # The release's lock has version 1. Its next commit writes version 3, as
    # nixpkgs applies it.
    http-server = {
      src = pkgs.applyPatches {
        name = "http-server-source";
        src =
          github "http-party" "http-server" "v14.1.1"
            "sha256-M/YC721QWJfz5sYX6RHm1U9WPHVRBD0ZL2/ceYItnhs=";
        patches = [
          (pkgs.fetchpatch {
            name = "regenerate-package-lock.patch";
            url = "https://github.com/http-party/http-server/commit/0cbd85175f1a399c4d13c88a25c5483a9f1dea08.patch";
            hash = "sha256-hJyiUKZfuSaXTsjFi4ojdaE3rPHgo+N8k5Hqete+zqk=";
          })
        ];
      };
      check = ''
        test "$(http-server -v)" = v14.1.1
      '';
    };
    # The source says version 0.0.0-development, which semantic-release
    # replaces at publication, so `nodemon --version` names no release.
    nodemon = {
      src = github "remy" "nodemon" "v3.1.14" "sha256-Kk42VjCmbkAfYzZEh4njyA4fodjmeCxIDraAZ8Hsd4g=";
      check = ''
        nodemon --help > help
        grep -q "Usage: nodemon" help
      '';
    };
    # TypeScript, bundled by vite in `npm run build`.
    npm-check-updates = {
      src =
        github "raineorshine" "npm-check-updates" "v23.0.2"
          "sha256-XCUtMEYEkBvn5Y32uLrbvCdpSTXOEdq2Pf9tPo6JWLI=";
      arguments.program = "ncu";
      check = ''
        test "$(ncu --version)" = 23.0.2
      '';
    };
    # TypeScript, compiled by tsc in `npm run build`.
    json-server = {
      src =
        github "typicode" "json-server" "v1.0.0-beta.3"
          "sha256-NDSmlW5SiA81Tf6kfkaaKUh05aGZEBajqkLZoxBv0Wc=";
      check = ''
        json-server --help > help
        grep -q Usage help
      '';
    };
    # pnpm 10, with `onlyBuiltDependencies`. TypeScript, by `tsc --build`.
    concurrently = {
      src = pkgs.concurrently.src;
      lock = "pnpm";
      check = ''
        test "$(concurrently --version)" = 9.2.1
      '';
    };
    # pnpm 11, with `allowBuilds`. Bundled by tsdown.
    bumpp = {
      src = pkgs.bumpp.src;
      lock = "pnpm";
      check = ''
        bumpp --version > version
        grep -q 12.2.0 version
      '';
    };
    # pnpm 10. Bundled by unbuild. It reads the tags of a git repository.
    changelogen = {
      src = pkgs.changelogen.src;
      lock = "pnpm";
      check = ''
        git init -q .
        git remote add origin https://github.com/unjs/changelogen.git
        git -c user.name=pydd -c user.email=pydd@localhost commit -q --allow-empty -m "feat: start"
        git tag v0.1.0
        git -c user.name=pydd -c user.email=pydd@localhost commit -q --allow-empty -m "feat: add a greeting"
        changelogen --from v0.1.0 --no-output > log 2>&1
        grep -q "Add a greeting" log || { cat log; exit 1; }
      '';
    };
    # Yarn v1. TypeScript, by tsc.
    diff2html-cli = {
      src = pkgs.diff2html-cli.src;
      lock = "yarn";
      arguments.program = "diff2html";
      check = ''
        printf -- '--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n' > change.diff
        diff2html -i file -o stdout -- change.diff > page.html
        grep -q "new" page.html
      '';
    };
    # Yarn v1. Compiled by babel.
    gitmoji-cli = {
      src = pkgs.gitmoji-cli.src;
      lock = "yarn";
      arguments.program = "gitmoji";
      check = ''
        gitmoji --help > help
        grep -q gitmoji help
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
      PATH = "${root}/bin:${pkgs.coreutils}/bin:${pkgs.gnugrep}/bin:${pkgs.git}/bin";
      args = [
        "-c"
        ''
          set -eu
          cd "$(mktemp -d)"
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
