# A wheel node takes `patches` and `postInstall` (nixidae/nixidae#1174).
# `mdurl` is a pure wheel of `pure-app`; the patch adds a module, and the
# hook writes a file beside it. Both must be in the environment.
{
  pkgs,
  pydd,
}:

let
  planner = pydd.python.uv {
    project = ./pure-app;
    venvName = "wheel-override";
    overrides.mdurl = {
      patches = [ ./wheel-override.patch ];
      postInstall = "echo hook > mdurl/_pydd_hook.txt";
    };
  };
  venv = planner.dynamic;
in
(derivation {
  name = "wheel-override-check";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.bash}/bin/bash";
  args = [
    "-c"
    ''
      set -eu
      "${venv}/bin/python" -c '
      import pathlib
      import mdurl, mdurl._pydd_patched
      assert mdurl._pydd_patched.PATCHED
      hook = pathlib.Path(mdurl.__file__).parent / "_pydd_hook.txt"
      assert hook.read_text() == "hook\n", hook
      print("patched and hooked")
      ' > "$out"
    ''
  ];
})
// {
  inherit planner;
}
