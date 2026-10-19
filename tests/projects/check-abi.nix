# Build `projects/abi-probe` for one interpreter, and read which wheel it took.
#
# The planner runs a GIL `python3`, so a free-threaded target gets its ABI from
# `PYDD_TARGET` or not at all (nixidae/nixidae#842).
{
  pkgs,
  pydd,
  project ? ./abi-probe,
  python ? pkgs.python313,
  expected ? "gil",
}:

let
  planner = pydd.python.uv { inherit project python; };
  venv = planner.dynamic;
in
(derivation {
  name = "abi-check";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.bash}/bin/bash";

  args = [
    "-c"
    ''
      set -eu
      said="$("${venv}/bin/python" -c 'import abiprobe; print(abiprobe.ABI)')" || {
        echo "abiprobe does not import" >&2
        exit 1
      }
      echo "abiprobe  $said" > "$out"
      [ "$said" = "${expected}" ] || { echo "the environment took the $said wheel, not ${expected}" >&2; exit 1; }
    ''
  ];
})
// {
  inherit planner;
}
