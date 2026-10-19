# Build a probe project for one interpreter, and read which wheel it took.
#
# Each wheel of a probe's index names itself in one attribute of one module.
# `abi-probe` names the ABI, which a free-threaded target gets from
# `PYDD_TARGET` or not at all (nixidae/nixidae#842), and a PyPy target gets a
# `pp311` wheel and an interpreter with no `python3` (#845, #855). `platform-probe` names
# the platform tag, which the target's libc decides (nixidae/nixidae#843).
{
  pkgs,
  pydd,
  project,
  probe,
  expected,
  python ? pkgs.python313,
}:

let
  planner = pydd.python.uv { inherit project python; };
  venv = planner.dynamic;
  module = builtins.head (pkgs.lib.splitString "." probe);
in
(derivation {
  name = "probe-check";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.bash}/bin/bash";

  args = [
    "-c"
    ''
      set -eu
      said="$("${venv}/bin/python" -c 'import ${module}; print(${probe})')" || {
        echo "${module} does not import" >&2
        exit 1
      }
      echo "${probe}  $said" > "$out"
      [ "$said" = "${expected}" ] || { echo "the environment took the $said wheel, not ${expected}" >&2; exit 1; }
    ''
  ];
})
// {
  inherit planner;
}
