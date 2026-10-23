{
  lib,
  stdenvNoCC,
  python,
  sphinx,
  myst-parser,
  furo,
  graphviz,
  nixvenv,
}:
let
  pythonEnv = nixvenv {
    inherit python;
    packages = _: [
      sphinx
      myst-parser
      furo
    ];
    executables = [ "sphinx-build" ];
  };
in
stdenvNoCC.mkDerivation {
  pname = "pydd-docs";
  version = "0";

  src = lib.fileset.toSource {
    root = ../.;
    fileset = ../docs;
  };

  nativeBuildInputs = [
    pythonEnv
    graphviz
  ];

  buildPhase = ''
    runHook preBuild
    sphinx-build -b html docs "$out" -W
    runHook postBuild
  '';

  dontInstall = true;
}
