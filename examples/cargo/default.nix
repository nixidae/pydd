# A Cargo project that links OpenSSL and zstd from nixpkgs, with no
# declaration: nixpkgs' `defaultCrateOverrides` names what openssl-sys and
# zstd-sys need. The planner asks cargo for its unit graph, and writes one
# node for each library, build script, proc-macro and binary.
{
  pydd,
  runCommand,
}:
let
  demo = pydd.rust.cargo { project = ./project; };
in
demo
// {
  # The binary, run; and the project's own tests, one node for each test
  # binary and one for the doctests.
  check = runCommand "cargo-demo-check" { inherit (demo) tests; } ''
    ${demo}/bin/cargo-demo > $out
    ls $tests >> $out
  '';
}
