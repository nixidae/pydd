# The root of an environment as an ordinary derivation (nixidae/nixidae#788):
# `override` over the arguments of `uv.nix`, then `overrideAttrs` with an
# install hook, then a run of Python from the tree that the root holds.
{
  pkgs,
  pydd,
  project ? ./pure-app,
  venvName ? "root-derivation",
}:

let
  base = pydd.python.uv {
    inherit project;
    venvName = "root-before-override";
  };
  root = (base.override { inherit venvName; }).overrideAttrs (old: {
    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.makeWrapper ];
    postInstall = (old.postInstall or "") + ''
      echo "the hook ran" > "$out/hooked"
    '';
    # A program of the root is a link into the dynamic output.
    postFixup = (old.postFixup or "") + ''
      wrapProgram "$out/bin/python" --set PYDD_WRAPPED yes
    '';
  });
in
assert root.name == venvName;
(derivation {
  name = "${venvName}-check";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.bash}/bin/bash";
  PATH = "${pkgs.coreutils}/bin";
  args = [
    "-c"
    ''
      set -eu
      report() {
        cat "${root}/hooked" || return 1
        prefix="$("${root}/bin/python" -c 'import sys; print(sys.prefix)')" || return 1
        echo "prefix       $prefix"
        if [ "$prefix" != "${root}" ]; then
          echo "the interpreter of the root reads another environment" >&2
          return 1
        fi
        "${root}/bin/python" -c 'import rich; print("rich", rich.__file__)' || return 1
        wrapped="$("${root}/bin/python" -c 'import os; print(os.environ.get("PYDD_WRAPPED"))')" || return 1
        echo "wrapped      $wrapped"
        if [ "$wrapped" != yes ]; then
          echo "the wrapper of postFixup does not run" >&2
          return 1
        fi
      }
      if ! report > "$out" 2>&1; then
        while IFS= read -r line; do printf '%s\n' "$line" >&2; done < "$out"
        exit 1
      fi
    ''
  ];
})
// {
  # The environment, for `test-projects` to compare across writers.
  planner = root;
}
