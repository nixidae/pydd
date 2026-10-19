# Released Nix: a planner writes one derivation, so the host makes the rest
# (nixidae/nixidae#783). `../python/planner.nix` calls this for a Python
# environment with `protocol = "out"`, and `pydd.modes.out` is the half that
# runs in the planner.
#
# Returns `menu`, which every planner of the environment takes, and `inputs`,
# which only the planner of the environment takes.
{
  pkgs,
  python,
  # What `../python/planner.nix` gives every node and every planner.
  tools,
  scripts,
  entries,
  installer,
  editables,
  uvEditables,
  mkPythonPlanner,
  plannerEnv,
  menu,
  venvName,
  application,
}:
let
  # The form that `built` of `../python/planner.nix` gives a tool, with its
  # context, as the host evaluation of a node needs it.
  hostTools = builtins.mapAttrs (
    _: tool:
    if builtins.isList tool then map (one: { out = "${one}"; }) tool else { out = "${tool}"; }
  ) tools;

  # **On released Nix the host makes each node that a planner names, except
  # the root.** A planner there writes one derivation, so every other one has
  # to be an input of the planner. The host evaluates `graph.nix` over the same
  # document that the planner would write for the node, with the context
  # still attached, so the two are one `.drv`. `OutWriter` refuses a root that
  # names a node the host did not make.
  #
  # Every wheel of the lock file gets a node, for every platform, because only
  # the planner chooses. A node is a derivation, not a build: Nix builds the
  # ones that the root names.
  normalise =
    name:
    pkgs.lib.concatStringsSep "-" (
      builtins.filter (part: builtins.isString part && part != "") (
        builtins.split "[-_.]+" (pkgs.lib.toLower name)
      )
    );

  # `Artifact.is_platform_wheel`: no tag of the wheel names the platform `any`.
  isPlatformWheel =
    filename:
    let
      tags = pkgs.lib.last (pkgs.lib.splitString "-" (pkgs.lib.removeSuffix ".whl" filename));
    in
    !(builtins.elem "any" (pkgs.lib.splitString "." tags));

  # `Artifact.as_plan` of a wheel, with the context that the planner puts back.
  hostArtifact =
    entry:
    {
      inherit (entry) filename package version;
    }
    // (if entry ? path then { inherit (entry) path; } else { out = "${entry.drv}"; });

  wheelNode = entry: {
    name = "${entry.package}-${entry.version}";
    kind = "wheel";
    platform = isPlatformWheel entry.filename;
    artifact = hostArtifact entry;
  };

  wheels = builtins.filter (entry: entry.kind == "wheel") entries;

  # `choose` of the planner takes the best wheel of the installer. The host
  # cannot rank wheels, so it takes the lock file's only one.
  installerWheel =
    let
      every = builtins.filter (entry: normalise entry.package == normalise installer) wheels;
      # The project's own installer before pydd's (`../python/build-systems`),
      # as the planner chooses.
      own = builtins.filter (entry: !(entry.buildSystems or false)) every;
      found = if own != [ ] then own else every;
    in
    if builtins.length found == 1 then
      builtins.head found
    else
      throw (
        "planner.nix: on released Nix the host needs exactly one wheel of the installer "
        + "'${installer}' in the lock file, and it holds ${toString (builtins.length found)}: "
        + toString (map (entry: entry.filename) found)
      );

  bootstrapNode = wheelNode installerWheel // {
    name = "${installerWheel.package}-${installerWheel.version}-bootstrap";
    kind = "unpack";
  };

  hostNode =
    node:
    import ../python/graph.nix {
      inherit (pkgs.stdenv.hostPlatform) system;
      inherit (python) pythonVersion;
      tools = hostTools;
      inherit scripts;
      nodes = [
        bootstrapNode
        node
      ];
      installer = bootstrapNode.name;
      root = node.name;
    };

  # The `.drv` of each host node, as an input of the planner and not a build.
  hostNodes = map (node: builtins.unsafeDiscardOutputDependency (hostNode node).drvPath) (
    [ bootstrapNode ] ++ map wheelNode wheels
  );

  # A planner, as a node of another graph names it: the `.drv` as an input,
  # and the output path with no dependency, as `row` gives a fetch.
  plannerRow = planner: {
    plannerDrv = builtins.unsafeDiscardOutputDependency planner.drvPath;
    plannerOut = builtins.unsafeDiscardStringContext planner.outPath;
  };

  # **On released Nix a local project is the root of a planner of its own.**
  # Its backend is declared in its tree, so no evaluation knows its node. Each
  # such planner runs the same selection as the environment and submits only
  # the node of one project, `PYDD_ONLY`. A project built from its own sdist
  # takes two: the archive, and the wheel out of it, which names the first.
  #
  # A planner of a local project gets no planner of another local project: two
  # members that need each other would make two planners that need each other.
  localSpecs = editables // uvEditables;
  suffixOf = {
    editable = "editable";
    wheel = "from-tree";
    sdist = "sdist";
    from-sdist = "from-sdist";
  };
  localPlanner =
    project: form: planned:
    (mkPythonPlanner {
      # Nix requires a planner to carry the name of what it submits.
      name = "${project}-${suffixOf.${form}}";
      main = "_environment";
      env = plannerEnv // {
        PYDD_INPUTS = builtins.toJSON (menu // { inherit planned; });
        PYDD_VENV_NAME = venvName;
        PYDD_ONLY = "${project}:${form}";
      };
    }).planner;
  localPlanned = pkgs.lib.concatMapAttrs (
    project: spec:
    let
      mode = spec.mode or "editable";
    in
    if mode == "sdist" then
      let
        archive = {
          "${project}:sdist" = plannerRow (localPlanner project "sdist" { });
        };
      in
      archive
      // {
        "${project}:from-sdist" = plannerRow (localPlanner project "from-sdist" archive);
      }
    else
      { "${project}:${mode}" = plannerRow (localPlanner project mode { }); }
  ) localSpecs;

  # **On released Nix the environment under an application is a planner of its
  # own**, because the planner of the application writes only the application.
  # It is the planner of the environment with no `application`, rooted at the
  # name that `Plan.root` gives the environment.
  venvPlanner =
    (mkPythonPlanner {
      name = "${venvName}-env";
      main = "_environment";
      env = plannerEnv // {
        PYDD_INPUTS = builtins.toJSON (
          menu
          // {
            planned = localPlanned;
            application = null;
          }
        );
        PYDD_VENV_NAME = "${venvName}-env";
      };
    }).planner;

in
{
  menu = { inherit hostNodes; };
  inputs = {
    planned = localPlanned;
  }
  // pkgs.lib.optionalAttrs (application != null) {
    venvPlanner = plannerRow venvPlanner;
  };
}
