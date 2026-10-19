# Run the program that `script.nix` made out of `projects/script-app/hello.py`.
#
# **The proof is that the script runs with what its block declared.** It
# imports `rich`, and only the environment that `hello.py.lock` pins holds it.
{
  pkgs,
  pydd,
}:

let
  planner = pydd.python.script { script = ./script-app/hello.py; };
in
(derivation {
  name = "script-check";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.bash}/bin/bash";
  args = [
    "-c"
    ''
      set -eu
      said="$(${planner}/bin/hello)"
      if [ "$said" != "hello from a script" ]; then
        echo "bin/hello said '$said'" >&2
        exit 1
      fi
      echo "$said" > "$out"
    ''
  ];
})
// {
  inherit planner;
}
