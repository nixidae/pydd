# Real Cargo projects, pinned, built as they are on GitHub: live practice for
# what a Cargo project in the wild needs. Each entry says what, if anything, it
# needs beyond the project itself.
#
#     nix run --file . internal.run -- rust-live.cli.ripgrep
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
    # A workspace of ten crates, with a build script that writes the man page.
    ripgrep = {
      src = github "BurntSushi" "ripgrep" "15.2.0" "sha256-BsSIbZwB6s8i3dDTRYJ1EdVbJmiO0oxcLu6qiYlPkOI=";
      arguments.program = "rg";
      check = ''
        echo needle > haystack
        rg --version
        test "$(rg needle haystack)" = needle
      '';
    };
    # jemalloc through tikv-jemalloc-sys, which builds C with make.
    fd = {
      src = github "sharkdp" "fd" "v10.5.0" "sha256-X3yrZUBieMO5bauUu2mskfvicLRdQcjbXouFTpz+0eQ=";
      arguments.program = "fd";
      check = ''
        mkdir tree && touch tree/found.txt
        fd --version
        test "$(fd found tree)" = tree/found.txt
      '';
    };
    hyperfine = {
      src = github "sharkdp" "hyperfine" "v2.0.0" "sha256-VCan4dLZAG3bOPlZfh2ydmrQHR9K/74zbOyOXRZGMUA=";
      check = ''
        hyperfine --version
        hyperfine --runs 2 --shell=none true > /dev/null
      '';
    };
    # libgit2 and oniguruma, through their -sys crates.
    bat = {
      src = github "sharkdp" "bat" "v0.26.1" "sha256-IbTvFT37BFo0tKOiApDL9sT+/nMD33MO3TXuho+lF2c=";
      check = ''
        bat --version
        echo 'fn main() {}' > main.rs
        bat --color=always --paging=never main.rs > /dev/null
      '';
    };
  };
in
builtins.mapAttrs (
  name: entry:
  let
    root = pydd.rust.cargo ({ project = entry.src; } // entry.arguments or { });
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
) projects
