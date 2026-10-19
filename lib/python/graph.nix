# The graph, as a function of the plan that `plan.py` decided.
#
# **`plan.py` decides what the graph is, and this file says how a node is
# built.** The split is the whole argument of pydd: every packaging decision
# needs `packaging`, which is Python, and every build step needs a derivation,
# which is Nix. Neither half reimplements the other.
#
# The evaluator that reads this file runs **inside** the sandbox of the
# planner. It writes each derivation below through the restricted daemon
# socket, and the planner then submits the root.
{
  system,
  pythonVersion,
  # Each tool as `{ drv, out, output }`, the way an artefact travels, so a
  # node reads the tool's output and names its derivation as an input.
  tools,
  # The store path of each build script, on the same terms.
  scripts,
  # One entry for each node: `{ name, kind, artifact, backend, backendPath }`.
  nodes,
  # The name of the node that holds `pypa/installer`.
  installer,
  # The name of the node that this graph is built for. Every other node is a
  # dependency of that one. The planner of the environment roots its document
  # at the environment; a planner of one distribution roots its document at
  # that distribution.
  root,
  # nixpkgs, imported by the planner, or `null`. `mkSource` gives the reason.
  pkgs ? null,
  # stdenv as data, or `null`: what `stdenv.mkDerivation` passes to
  # `derivation`, from `./stdenv-template.nix`. `mkSource` gives the reason.
  stdenv ? null,
}:

let
  # **No store path here needs `builtins.storePath` or `builtins.appendContext`.**
  # Both call `EnsurePath`, which a `builder-rpc-v0` socket refuses, so the
  # planner attaches the context before this file sees a value
  # (`pydd.modes.evaluate._document.document_value`).
  tool = builtins.mapAttrs (
    _: built: if builtins.isList built then map (one: one.out) built else built.out
  ) tools;
  script = scripts;

  # **This is what makes a pre-instantiated fetch a real dependency.**
  # `planner.nix` hands the planner the store path of each `fetchurl`
  # derivation and the store path of its output, with the dependency between
  # them discarded, so that Nix instantiates every artefact of the lock file
  # and downloads none of them. The planner gives `out` the context of `drv`
  # for the artefacts that it chose.
  # An artefact that carries a `path` is already present, because some
  # derivation named it as an input. A planner of one source distribution
  # receives its own archive that way, and needs no fetch for it.
  fetched = artifact: if artifact ? path then artifact.path else artifact.out;

  # Every node floats. A floating output has no path until the build gives it
  # one, so a node that reads another node interpolates a placeholder, and
  # neither the planner nor this file does any hash arithmetic.
  floating = {
    __contentAddressed = true;
    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
  };

  site = "lib/python${pythonVersion}/site-packages";

  # **`bash` is on the path, and not only the builder.** A backend that runs
  # Ninja or Make starts each command through `sh`, and the Ninja of nixpkgs
  # finds `sh` on `PATH` and never at `/bin/sh`.
  basePath = "${tool.coreutils}/bin:${tool.unzip}/bin:${tool.gnutar}/bin:${tool.gzip}/bin:${tool.python}/bin:${tool.bash}/bin";

  mk =
    args:
    derivation (
      {
        inherit system site;
        builder = "${tool.bash}/bin/bash";
        PATH = basePath;
        # **Without this, no node of this graph is reproducible.**
        # `installer` compiles bytecode with `compileall.compile_file`, and
        # `py_compile._get_default_invalidation_mode` chooses `TIMESTAMP` when
        # this variable is empty. A timestamp `.pyc` records the mtime of the
        # source at build time, so each build writes different bytes, the
        # floating output takes a new path, and every node above it rebuilds.
        # The variable makes the mode `CHECKED_HASH`, which records a hash of
        # the source instead. `stdenv` of nixpkgs sets the same value, and a
        # node of this graph runs no `stdenv`.
        SOURCE_DATE_EPOCH = "315532800";
      }
      // args
      // floating
    );

  # **What a node needs to patch a binary, and nothing that a node does not.**
  # `auto-patchelf` reads the loader of this system from the `nix-support` of
  # the bintools wrapper, and it calls `patchelf`, so both belong to the node
  # that patches. A pure Python wheel gets none of this, and so takes no
  # dependency on a compiler library at all.
  patchelfInputs = {
    PATH = "${basePath}:${tool.patchelf}/bin";
    autoPatchelf = "${tool.autoPatchelf}/bin/auto-patchelf";
    NIX_BINTOOLS = "${tool.bintools}";
    # What a manylinux binary may assume the system has (`manylinux` of
    # `planner.nix`). `auto-patchelf` adds the libc of this system on its own,
    # from `orig-libc`.
    patchelfLibs = builtins.concatStringsSep " " (map (library: "${library}/lib") tool.manylinux);
  };

  # **What one package needs to build, and what no other node gets.** A source
  # distribution that holds C code needs a compiler, and `basePath` above holds
  # none: a graph that gave every node a compiler would put one in the closure
  # of every pure Python wheel as well.
  #
  # `planner.nix` takes the list by package name, at its `buildTools` argument,
  # and the planner attaches it to the node that builds that package. So this
  # file looks nothing up, and it canonicalises no name: `packaging` does that,
  # in the half of this example that is Python.
  #
  # **`CPPFLAGS` and `LDFLAGS` are the variables, and `NIX_CFLAGS_COMPILE` is
  # not.** `cc-wrapper` reads a name that carries the salt of its platform,
  # `NIX_CFLAGS_COMPILE_x86_64_unknown_linux_gnu`, and it copies the plain name
  # into that one only when `accumulateRoles` finds
  # `NIX_CC_WRAPPER_TARGET_HOST_<salt>`. The setup hook of the wrapper exports
  # that variable, and a node of this graph runs no setup hook. So a plain
  # `NIX_LDFLAGS` here reaches nothing, and `docs/PACKAGING-FACTS.md` records
  # the measurement that found it.
  #
  # `customize_compiler` of `setuptools._distutils` reads the two below, and
  # appends each one. It reads `CFLAGS` as well, and **replaces** the value
  # that `sysconfig` gives, so this file does not set that one.
  #
  # A directory that an entry does not hold costs nothing. `cc` ignores an
  # `-isystem` and an `-L` that name no directory, and `pkg-config` ignores
  # such an entry of its path.
  buildToolsOf =
    node:
    let
      roots = map (built: built.out) (node.buildTools or [ ]);
      join = separator: render: builtins.concatStringsSep separator (map render roots);
    in
    if roots == [ ] then
      { }
    else
      {
        PATH = "${join ":" (root: "${root}/bin")}:${basePath}";
        # **`-g0` keeps the compiler out of the runtime closure.** A `.so` with
        # debug information records every include directory in its DWARF
        # sections, so the scan of the output finds the compiler, `glibc-dev`
        # and the `dev` output of each library, and each one enters the closure
        # of the environment. `stdenv` of nixpkgs strips a binary for the same
        # reason, and a node of this graph runs no `stdenv`.
        # `docs/PACKAGING-FACTS.md` gives the measurement.
        CPPFLAGS = "-g0 ${join " " (root: "-isystem ${root}/include")}";
        LDFLAGS = join " " (root: "-L${root}/lib");
        PKG_CONFIG_PATH = join ":" (root: "${root}/lib/pkgconfig");
      }
      # **A node that builds with tools also fixes the RUNPATH of what it
      # built.** `scripts/fixup-elf.py` gives the reason (nixidae/nixidae#785).
      // (
        if node.overrides.fixup or true then
          {
            fixupElf = script.fixupElf;
            fixupPaths = join ":" (root: "${root}/lib");
            patchelf = "${tool.patchelf}/bin/patchelf";
          }
        else
          { }
      );

  # **What one package needs beyond its tools, as data** (nixidae/nixidae#786).
  # `overrides.nix` reads it from the project and the caller, and the planner
  # attaches it to the node that builds that package. The build scripts read:
  #
  #   patches         applied with `patch -p1` in the project, before the build
  #   preBuild        shell, run in the project before the build; it may export
  #   configSettings  PEP 517 `config_settings`, as JSON
  #   postInstall     shell, run in the project after the install, with `$out`
  #
  # `env` comes last, so it can replace any variable above, `PATH` included.
  # A store path in `env` is a dependency only when it is the whole value;
  # a build tool is the way to name one inside a flag.
  overridesOf =
    node:
    let
      override = node.overrides or { };
    in
    builtins.intersectAttrs {
      configSettings = null;
      preBuild = null;
      postInstall = null;
    } override
    // (
      if override ? patches then
        {
          patches = builtins.concatStringsSep " " (map (patch: patch.out) override.patches);
          patch = "${tool.patch}/bin/patch";
        }
      else
        { }
    )
    // override.env or { };

  # **A source build, with `stdenv` when the planner has nixpkgs.** Only the
  # `recursive-nixpkgs` mode passes `pkgs`: its planner imports nixpkgs from
  # an input store path, which recursive-nix allows. stdenv's hooks then do
  # what `buildToolsOf` and `fixup-elf.py` do by hand: the compiler wrapper
  # finds each tool's headers and libraries, and `autoPatchelfHook` fixes the
  # RUNPATH. The command runs in a subshell, so the script's `set -eu` stays
  # out of stdenv's own shell. The output differs from a node of `mk`:
  # stdenv strips and patches what it built.
  #
  # **stdenv as data does the same with no nixpkgs in the planner.** The host
  # evaluates `stdenv.mkDerivation` once, and passes what it hands to
  # `derivation`. A node is that template with its own attributes on top: the
  # derivation `mkDerivation` would write, in every mode, `builder-rpc-v0` and
  # released Nix included. It keeps stdenv's phases and hooks, and loses
  # `mkDerivation`'s logic at evaluation: `overrideAttrs`, `passthru`, and
  # inputs that `mkDerivation` would derive from others.
  mkSource =
    node: args:
    let
      roots = map (built: built.out) (node.buildTools or [ ]);
      attrs =
        hooks:
        removeAttrs args [ "args" ]
        // overridesOf node
        // floating
        // {
          inherit site;
          nativeBuildInputs =
            roots
            ++ hooks
            ++ [
              tool.python
              tool.unzip
            ];
          buildInputs = roots;
          dontUnpack = true;
          dontConfigure = true;
          dontInstall = true;
          buildPhase = "(${builtins.elemAt args.args 1})";
        };
    in
    if pkgs != null then
      pkgs.stdenv.mkDerivation (attrs [ pkgs.autoPatchelfHook ])
    else if stdenv != null then
      derivation (stdenv // attrs stdenv.nativeBuildInputs)
    else
      mk (args // buildToolsOf node // overridesOf node);

  # The `site-packages` of a node that this graph builds. A node output floats,
  # so this is a downstream placeholder until the node is built.
  sitePath = name: "${built.${name}}/${site}";

  # **The installer is a node, so every install waits for it.** `pypa/installer`
  # is what writes `RECORD`, the `.dist-info` and each console script, and the
  # planner resolved it from the lock file like every other artefact.
  installerPath = sitePath installer;

  build =
    node:
    if node.kind == "unpack" then
      # The bootstrap. Nothing can install the installer, so its node unzips
      # the wheel and puts it on a path.
      mk {
        name = node.name;
        args = [
          "-c"
          ". ${script.unpackWheel}"
        ];
        wheel = fetched node.artifact;
      }
    else if node.kind == "wheel" then
      mk (
        {
          name = node.name;
          args = [
            "-c"
            ". ${script.installWheel}"
          ];
          wheel = fetched node.artifact;
          wheelName = node.artifact.filename;
          inherit installerPath;
        }
        # **The planner read the tag of the wheel, so it knows this.** A wheel
        # whose platform tag is not `any` carries a binary that was linked
        # against another Linux.
        // (if node.platform then patchelfInputs else { })
      )
    else if node.kind == "editable" then
      mkSource node (
        {
          name = node.name;
          args = [
            "-c"
            ''exec "${tool.python}/bin/python3" "${script.buildLocal}"''
          ];
          mode = "editable";
          source = node.source;
          subdirectory = node.subdirectory or "";
          # A literal path names one tree forever. A reference to a variable is
          # the same bytes for every tree, so one environment reads whichever
          # tree the variable names, and nothing rebuilds.
          editableRoot = node.root;
          inherit installerPath;
          inherit (node) backend;
          backendPath = builtins.concatStringsSep ":" (map sitePath node.backendPath);
        }
      )
    else if node.kind == "local-wheel" then
      # **A wheel out of a tree, with no archive between the two.** This is what
      # `pip install .` does, and what a git source of `uv.lock` needs: a
      # checkout is a directory, and a `build_sdist` step before the wheel would
      # only write a file that nothing reads.
      #
      # The install holds the code, so it follows no tree of the user. That is
      # the difference between this node and a node of kind `editable`, and it
      # is the difference that `uv` makes between a git source and a workspace
      # member.
      mkSource node (
        {
          name = node.name;
          args = [
            "-c"
            ''exec "${tool.python}/bin/python3" "${script.buildLocal}"''
          ];
          mode = "wheel";
          source = node.source;
          subdirectory = node.subdirectory or "";
          inherit installerPath;
          inherit (node) backend;
          backendPath = builtins.concatStringsSep ":" (map sitePath node.backendPath);
        }
      )
    else if node.kind == "local-sdist" then
      # **The distribution of a local project, as its own store object.** This
      # is what `uv build` writes and what an index serves. The node runs one
      # PEP 517 hook, `build_sdist`, and it installs nothing: a node of kind
      # `sdist-build` reads this output and builds the wheel out of it.
      #
      # **The two hooks are two nodes on purpose.** The archive is the thing
      # that a project ships, so it is worth a store path that a reader can
      # open. It also makes the failure legible: an archive that leaves a file
      # out fails in the node below this one, and this node still succeeded.
      mkSource node (
        {
          name = node.name;
          args = [
            "-c"
            ''exec "${tool.python}/bin/python3" "${script.buildLocal}"''
          ];
          mode = "sdist";
          source = node.source;
          subdirectory = node.subdirectory or "";
          inherit (node) backend;
          backendPath = builtins.concatStringsSep ":" (map sitePath node.backendPath);
        }
      )
    else if node.kind == "sdist" then
      # **A node of this kind is a planner, and not a build.** The build system
      # of a source distribution is declared inside the archive, and no
      # evaluation has downloaded the archive. `planner.nix` instantiated a
      # planner that does, and `builtins.outputOf` names what that plan builds.
      #
      # **The planner is instantiated outside, and it has to be.** A planner
      # cannot read a store path that the planner itself added, and
      # `builtins.outputOf` reads the derivation it is given. So a planner
      # written here would fail with "opening file ...: No such file or
      # directory". The `.drv` below is an input source of this planner, so the
      # read succeeds.
      if !(node.artifact ? plannerDrv) then
        throw (
          "${node.artifact.filename} needs a planner, and the menu has none. "
          + "A chain of source distributions ran past `sdistPlannerDepth` of planner.nix."
        )
      else
        builtins.outputOf node.artifact.plannerOut "out"
    else if node.kind == "planned" then
      # **A node that another planner writes, as the root of its graph.** On
      # released Nix a planner writes one derivation, so a node that only a
      # build can shape, such as a local project, is the root of a planner
      # that the host made. `_plan.Plan.planned_node` gives the reason.
      builtins.outputOf node.artifact.plannerOut "out"
    else if node.kind == "sdist-build" then
      # What the planner above decided: run one PEP 517 hook, with a backend
      # that is another node of the graph that the planner submitted.
      mkSource node (
        {
          name = node.name;
          args = [
            "-c"
            ". ${script.buildSdist}"
          ];
          # **The archive comes from the index, or from a node of this graph.**
          # `from` names the node of kind `local-sdist` that built it, and the
          # dependency is then an ordinary one of this evaluation. An artefact of
          # the menu needs `fetched` instead, which puts back the context that
          # `planner.nix` discarded.
          sdist = if node ? from then "${built.${node.from}}" else fetched node.artifact;
          # **An archive of a URL source does not always hold one project.**
          # `uv.lock` writes `subdirectory` for such a source, and `pypa/hatch` is
          # the case that this lab reads. An archive that a node of this graph
          # built holds one project at its top, so that path names nothing here.
          subdirectory = if node ? from then "" else (node.artifact.subdirectory or "");
          inherit (node) backend;
          inherit installerPath;
          # The backend comes from the nodes that this graph already builds, and
          # from nowhere else. `built.${name}` is a derivation of this same
          # evaluation, so the dependency is real and the order is right.
          backendPath = builtins.concatStringsSep ":" (map sitePath node.backendPath);
        }
      )
    else if node.kind == "nixpkgs" then
      # **This node builds nothing, and that is the whole point.** nixpkgs
      # already built the package for this system, so the graph takes the store
      # path and `make-venv.py` merges it like any other member.
      #
      # `fetched` is the same call that an artefact of the menu goes through:
      # the output arrives with no context, and `builtins.appendContext` makes
      # it a real dependency of whatever reads it. The outer store then
      # **substitutes** that output from a cache, because the derivation kept
      # the identity that nixpkgs gave it.
      #
      # No `patchelfInputs` reach this node. nixpkgs linked the package against
      # the store already, and a second pass of `auto-patchelf` would have
      # nothing to correct.
      fetched node.artifact
    else if node.kind == "host" then
      # **The escape hatch** (nixidae/nixidae#787). A function of the host made
      # this derivation for the artefact that the planner chose, so it is a
      # node of kind `nixpkgs` that a user wrote.
      fetched node.artifact
    else if node.kind == "venv" then
      # A real virtual environment, with `venv.EnvBuilder`, and the members
      # merged into it. `make-venv.py` runs under the interpreter that the
      # environment is for, which is what `EnvBuilder` takes as its base.
      mk {
        name = node.name;
        args = [
          "-c"
          ''exec "${tool.python}/bin/python3" "${script.makeVenv}"''
        ];
        members = builtins.concatStringsSep " " (map (name: "${built.${name}}") node.members);
        installed = builtins.concatStringsSep "\n" node.installed;
      }
    else if node.kind == "application" then
      # The programs of one package, linked out of the environment. The node
      # of kind `venv` holds the environment, and the node that installed the
      # package is the template of the shape. `make-application.py` gives the
      # whole reason, and `pyproject.nix` gives the idea.
      mk {
        name = node.name;
        args = [
          "-c"
          ''exec "${tool.python}/bin/python3" "${script.makeApplication}"''
        ];
        venv = "${built.${node.venv}}";
        bases = builtins.concatStringsSep " " (map (name: "${built.${name}}") node.bases);
      }
    else
      throw "unknown node kind '${node.kind}'";

  built = builtins.listToAttrs (
    map (node: {
      inherit (node) name;
      value = build node;
    }) nodes
  );
in
built.${root}
