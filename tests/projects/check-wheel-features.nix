# Run the environment of `projects/wheel-features`, whose wheels each carry one
# corner of the wheel format. `./check-index.nix` gives the reason that this
# runs as a derivation, and the shape of a registry of this machine.
{
  pkgs,
  pydd,
  project ? ./wheel-features,
}:

let
  planner = pydd.python.uv { inherit project; };
  venv = planner.dynamic;
in
(derivation {
  name = "wheel-features-check";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.bash}/bin/bash";

  args = [
    "-c"
    ''
      set -eu

      report() {
        "${venv}/bin/python" ${./scripts/check-wheel-features.py} || return 1

        # A console script, a GUI script and two `.data/scripts` scripts with
        # `#!python`: each must run the environment's interpreter. The spec
        # has a Unix installer add +x to a script a Windows build wrote.
        for script in datafull-cli datafull-gui datafull-raw datafull-windows; do
          said="$("${venv}/bin/$script")" || { echo "bin/$script failed" >&2; return 1; }
          read -r shebang < "${venv}/bin/$script"
          echo "bin/$script  $said  $shebang"
          [ "$said" = hello ] || { echo "bin/$script said '$said'" >&2; return 1; }
          [ "$shebang" = "#!${venv}/bin/python" ] || { echo "bin/$script runs $shebang" >&2; return 1; }
        done
      }

      if ! report > "$out" 2>&1; then
        while IFS= read -r line; do printf '%s\n' "$line" >&2; done < "$out"
        exit 1
      fi
    ''
  ];
})
// {
  inherit planner;
}
