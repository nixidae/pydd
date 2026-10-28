# A PEP 751 lock file, built as a graph of derivations.
#
# **This file makes no packaging decision**, as `./uv.nix` makes none. It reads
# `pylock.toml`, makes one fetch for every artefact of every package, and gives
# that list to `./planner.nix`. `pydd.python._pylock` decides, inside the
# planner, which packages this target installs.
#
# uv writes the file:
#
#     uv export --format pylock.toml --frozen --all-packages -o pylock.toml
#
# **`parse-toml-timestamps` is necessary.** PEP 751 writes `upload-time` as a
# TOML datetime, and without that experimental feature `builtins.fromTOML`
# fails with "Dates and times are not supported". `docs/PACKAGING-FACTS.md`
# gives the measurement, under "PEP 751".
#
# **One `pylock.toml` is one selection.** The exporter resolves the groups and
# the extras, so this reader takes no `select`, `extras` or `groups`. It also
# records no `[package.metadata]`, so no freshness check runs: a stale export
# builds what it says.
#
# **PEP 751 gives each source shape a key of its own**, so this reader asks
# which key is present and reads it. There is no url to parse, and no registry
# to join a path against.
{
  pkgs,
  # The pydd library. Its `writer` chooses the path; `./planner.nix` names both.
  pydd,
  # The directory that holds the lock file. Every relative path of the lock
  # file resolves against it, which is what PEP 751 says.
  project,
  # The name of the lock file. PEP 751 permits `pylock.toml` and
  # `pylock.<name>.toml`.
  lockName ? "pylock.toml",
  # The installer, which pydd's own lock supplies when the file holds none.
  installer ? "installer",
  # What an editable install of a directory redirects to. Each directory adds
  # its own path below it.
  editableRoot ? "$PYDD_EDITABLE_ROOT",
  # Packages to build from their sdist even when a wheel fits.
  noBinary ? [ ],
  # The name of the environment. A lock file names no project, so the default
  # is the name in `pyproject.toml` beside it.
  venvName ? null,
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
  # Whether an evaluator in a planner may build to import, as in `./uv.nix`.
  allowImportFromDerivation ? false,
  # Whether the planner falls back to pydd's lock of the installer and the
  # common build backends, as in `./uv.nix`.
  buildSystems ? true,
}:
let
  inherit (pkgs) lib;

  lockFile = project + "/${lockName}";
  document = builtins.fromTOML (builtins.readFile lockFile);

  # PEP 751 pins the version of the format. `packaging.pylock` refuses any
  # other, so the two halves agree on what they read.
  lockVersion = document.lock-version or null;
  packages = document.packages or [ ];

  pyprojectFile = project + "/pyproject.toml";
  pyproject =
    if builtins.pathExists pyprojectFile then
      builtins.fromTOML (builtins.readFile pyprojectFile)
    else
      { };
  projectName = pyproject.project.name or null;
  environmentName =
    if venvName != null then
      venvName
    else if projectName != null then
      projectName
    else
      throw "pylock.nix: ${toString project} holds no pyproject.toml with a name, so pass `venvName`";

  # uv writes an absolute `file://` url for a registry that is a directory.
  fileScheme = "file://";
  isFileUrl = url: lib.hasPrefix fileScheme url;
  isLocal = entry: entry ? path || isFileUrl (entry.url or "");

  fileName =
    entry:
    if entry ? name then
      entry.name
    else if entry ? path then
      baseNameOf entry.path
    else
      lib.last (lib.splitString "/" entry.url);

  # PEP 751 writes a table of algorithm to digest, and `fetchurl` takes one.
  hashOf =
    entry:
    let
      hashes = entry.hashes or { };
      names = builtins.attrNames hashes;
    in
    if hashes ? sha256 then
      "sha256:${hashes.sha256}"
    else if names == [ ] then
      throw "pylock.nix: ${fileName entry} carries no hash, so a fetch of it is not fixed"
    else
      "${builtins.head names}:${hashes.${builtins.head names}}";

  # `builtins.path` names the one file, so a second file of the same directory
  # is no input of the planner.
  localFile =
    entry:
    let
      relative = if entry ? path then entry.path else lib.removePrefix fileScheme entry.url;
    in
    builtins.path {
      path = if lib.hasPrefix "/" relative then /. + relative else project + "/${relative}";
      name = fileName entry;
    };

  artifactOf =
    package: kind: entry:
    {
      inherit kind;
      package = package.name;
      version = package.version or (throw "pylock.nix: '${package.name}' has an artefact and no version");
      filename = fileName entry;
    }
    // (
      if isLocal entry then
        { path = "${localFile entry}"; }
      else
        {
          drv = pkgs.fetchurl {
            inherit (entry) url;
            name = fileName entry;
            hash = hashOf entry;
          };
        }
    )
    // lib.optionalAttrs (entry ? subdirectory) { inherit (entry) subdirectory; };

  # **An archive is a source distribution that no index served**, and the
  # planner builds it as an `sdist`.
  artifactsOf =
    package:
    map (artifactOf package "wheel") (package.wheels or [ ])
    ++ map (artifactOf package "sdist") (lib.optional (package ? sdist) package.sdist)
    ++ map (artifactOf package "sdist") (lib.optional (package ? archive) package.archive);

  # A commit names no hash of the tree that it gives, so this fetch reaches the
  # network during evaluation, as it does for `uv.lock`.
  gitTree =
    package:
    builtins.fetchGit {
      inherit (package.vcs) url;
      rev = package.vcs.commit-id;
      # A commit of a tag is not in the default branch.
      allRefs = true;
    };

  isTree = package: package ? directory || package ? vcs;

  editables = lib.listToAttrs (
    map (package: {
      inherit (package) name;
      value =
        if package ? vcs then
          {
            src = gitTree package;
            # A checkout is not a tree that the user edits, so the install
            # holds the code.
            mode = "wheel";
            subdirectory = package.vcs.subdirectory or "";
          }
        else
          {
            src = builtins.path {
              path = project + "/${package.directory.path}";
              name = "${package.name}-source";
            };
            root =
              if package.directory.path == "." then editableRoot else "${editableRoot}/${package.directory.path}";
            # PEP 751 records whether the directory is editable, so this
            # reader follows it rather than a rule of its own.
            mode = if package.directory.editable or false then "editable" else "wheel";
            subdirectory = package.directory.subdirectory or "";
          };
    }) (lib.filter isTree packages)
  );

  sourceBuilt = map (package: package.name) (
    lib.filter (package: package ? sdist || package ? archive || isTree package) packages
  );
  hintedBuildTools = lib.optionalAttrs nixpkgsHints (
    import ./hints.nix { inherit lib pkgs python; } sourceBuilt
  );

  overridden = import ./overrides.nix {
    inherit lib pkgs project;
    projectName = environmentName;
    declared = pyproject.tool.pydd.overrides or { };
    inherit overrides;
  };

  ownArtifacts = lib.concatMap artifactsOf packages;
  fallback = import ./build-systems {
    inherit pkgs;
    own = ownArtifacts;
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
      editables
      ;
    buildTools = hintedBuildTools // overridden.buildTools // buildTools;
    overrides = overridden.nodes;
    inherit (overridden) dependencies;
    artifactOverrides = overridden.artifacts;
    artifacts = ownArtifacts ++ lib.optionals buildSystems fallback.rows;
    buildSystems = if buildSystems then fallback.lock else null;
    roots = [ ];
    venvName = environmentName;
    pylock = {
      lock = "${lockFile}";
    };
  };

  # An ordinary derivation, as `./uv.nix` gives it (nixidae/nixidae#788).
  root = import ../core/root.nix {
    inherit pkgs;
    name = environmentName;
    application = planned.out;
    passthru =
      builtins.removeAttrs planned [
        "out"
        # The root's own `overrideAttrs` is mkDerivation's, not the planner's.
        "overrideAttrs"
      ]
      // {
        dynamic = planned.out;
        env = root;
      };
  };
in
lib.throwIfNot (lockVersion == "1.0")
  "pylock.nix: ${toString lockFile} says lock-version ${builtins.toJSON lockVersion}, and this reader accepts \"1.0\""
  root
