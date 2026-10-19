# A PEP 723 script, built as its environment and a program that runs it.
#
# A script declares its dependencies in a `# /// script` block, and
# `uv lock --script <script>` pins them in `<script>.lock`, a `uv.lock` with a
# `[manifest] requirements` table and no project. This file fetches each
# artefact of that lock and gives the list to `./planner.nix`;
# `pydd.python._script` walks the lock from the requirements, and refuses a
# lock whose requirements are not the block's.
#
# **The lock holds registry packages only.** A git or path source fails here,
# by name. The result is the environment, with the script as `bin/<name>`.
{
  pkgs,
  pydd,
  # The script, as a path.
  script,
  # The lock that `uv lock --script` writes beside the script.
  lockFile ? script + ".lock",
  # The name of the program, and of the environment.
  name ? pkgs.lib.removeSuffix ".py" (baseNameOf script),
  installer ? "installer",
  noBinary ? [ ],
  python ? pkgs.python3,
  markers ? { },
  buildTools ? { },
  # Whether a package built from source takes its native inputs from the
  # matching package of `python.pkgs`, as in `./uv.nix`.
  nixpkgsHints ? true,
  fromNixpkgs ? { },
  # Per-package node overrides, as in `./uv.nix`.
  overrides ? { },
  # The escape hatch; `./planner.nix` gives the contract at `builders`.
  builders ? { },
  allowImportFromDerivation ? false,
  # Whether the planner falls back to pydd's lock of the installer and the
  # common build backends, as in `./uv.nix`.
  buildSystems ? true,
}:
let
  inherit (pkgs) lib;

  document = builtins.fromTOML (builtins.readFile lockFile);
  foreign = builtins.filter (package: !(package.source ? registry)) (document.package or [ ]);

  ownArtifacts = import ./registry-rows.nix { inherit pkgs document; };
  fallback = import ./build-systems {
    inherit pkgs;
    own = ownArtifacts;
  };

  sourceBuilt = map (package: package.name) (builtins.filter (package: package ? sdist) (document.package or [ ]));
  hintedBuildTools = lib.optionalAttrs nixpkgsHints (
    import ./hints.nix { inherit lib pkgs python; } sourceBuilt
  );
  overridden = import ./overrides.nix {
    inherit lib pkgs overrides;
    project = dirOf script;
    projectName = name;
    declared = { };
  };

  planned = import ./planner.nix {
    inherit
      pkgs
      pydd
      installer
      markers
      noBinary
      python
      fromNixpkgs
      builders
      allowImportFromDerivation
      ;
    buildTools = hintedBuildTools // overridden.buildTools // buildTools;
    overrides = overridden.nodes;
    inherit (overridden) dependencies;
    artifactOverrides = overridden.artifacts;
    artifacts = ownArtifacts ++ lib.optionals buildSystems fallback.rows;
    buildSystems = if buildSystems then fallback.lock else null;
    roots = [ ];
    venvName = name;
    script = {
      lock = "${lockFile}";
      source = "${script}";
    };
  };

  root = import ../core/root.nix {
    inherit pkgs name;
    application = planned.out;
    program = name;
    # The environment's own interpreter, which finds `pyvenv.cfg` beside it.
    # The script's own shebang is replaced, not kept below this one.
    postInstall = ''
      {
        echo "#!$application/bin/python"
        if [ "$(head -c 2 ${script})" = "#!" ]; then tail -n +2 ${script}; else cat ${script}; fi
      } > "$out/bin/${name}"
      chmod +x "$out/bin/${name}"
    '';
    passthru = builtins.removeAttrs planned [
      "out"
      "overrideAttrs"
    ] // {
      dynamic = planned.out;
      env = root;
    };
  };
in
lib.throwIf (foreign != [ ])
  "script.nix: ${toString lockFile} resolves ${
    lib.concatMapStringsSep ", " (package: "'${package.name}'") foreign
  } from a source that is not a registry, and this reader takes registry packages only"
  root
