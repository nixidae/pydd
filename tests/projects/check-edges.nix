# `script-app` with two overrides that change the resolution (nixidae/nixidae#850).
#
# `rich` drops `markdown-it-py` and `pygments`, and adds `mdurl`, which the lock
# reached only through `markdown-it-py`. So the environment holds exactly `rich`
# and `mdurl`: without the add it holds `rich` alone, and without the drop all
# four. `mdurl` installs from a wheel that a derivation made, and only that copy
# holds `mdurl._pydd_replaced`. With `mdurl` as `{ url, hash }` of the sdist,
# and `marker = false`, the replacement builds from source.
{
  pkgs,
  pydd,
  mdurl ? null,
  marker ? true,
}:

let
  marked = pkgs.runCommand "mdurl-0.1.2-py3-none-any.whl" { } ''
    ${pkgs.python3}/bin/python ${./scripts/mark-wheel.py} ${
      pkgs.fetchurl {
        url = "https://files.pythonhosted.org/packages/b3/38/89ba8ad64ae25be8de66a6d463314cf1eb366222074cfda9ee839c56a4b4/mdurl-0.1.2-py3-none-any.whl";
        hash = "sha256:84008a41e51615a49fc9966191ff91509e3c40b939176e643fd50a5c2196b8f8";
      }
    } mdurl $out
  '';
  planner = pydd.python.script {
    script = ./script-app/hello.py;
    name = if marker then "hello-edges" else "hello-edges-url";
    overrides = {
      rich.dependencies = {
        drop = [
          "markdown-it-py"
          "pygments"
        ];
        add = [ "mdurl" ];
      };
      mdurl.artifact = if mdurl == null then marked else mdurl;
    };
  };
in
(derivation {
  name = "edges-check";
  system = pkgs.stdenv.hostPlatform.system;
  builder = "${pkgs.bash}/bin/bash";
  args = [
    "-c"
    ''
      set -eu
      ${planner}/bin/${planner.name} > /dev/null
      found="$(${planner.dynamic}/bin/python -c '
      import importlib.metadata
      ${if marker then "import mdurl._pydd_replaced" else ""}
      print(" ".join(sorted(d.metadata["Name"].lower() for d in importlib.metadata.distributions())))
      ')"
      if [ "$found" != "mdurl rich" ]; then
        echo "the environment holds '$found', and the overrides leave 'mdurl rich'" >&2
        exit 1
      fi
      echo "$found" > "$out"
    ''
  ];
})
// {
  inherit planner;
}
