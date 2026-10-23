# A Python environment, resolved by `packaging` and built as a graph.
#
# **The Nix half here knows nothing about Python.** It reads the lock file,
# makes a `fetchurl` for every artefact, and hands the planner that menu. The
# planner decides which artefacts this target needs, and what the graph over
# them is. `graph.nix` says how one node is built.
#
# `./uv.nix` calls this for a uv project. `pydd.render` chooses the path from
# plan to derivations, and both paths give the same derivations:
#
#   nix     the planner evaluates `graph.nix` in its sandbox, in huggorm.
#   python  `pydd.modes.render` writes them in Python. With the `cli`
#           writer, no planner needs huggorm.
#
# `pydd.protocol` chooses the Nix: `rpc` is `builder-rpc-v0` on Nix master,
# and `out` is a release, where the host makes every node but the root.
{
  pkgs,
  # The pydd library. Its `writer` chooses the path.
  pydd,
  # A lock file of the flat form: `{ roots, installer, artifacts }`.
  lock ? {
    roots = [ ];
    installer = "installer";
    artifacts = [ ];
  },
  # **The seam between the reader and the graph.** One entry for each
  # artefact: the metadata a planner reads, and the derivation that produces
  # the file. `null` means "read the lock file above, and fetch each artefact".
  #
  # A reader of a different lock format fills this list, and so does a test
  # that builds its artefacts rather than fetching them.
  artifacts ? null,
  # pydd's own lock of the installer and the common build backends
  # (`./build-systems`), as a path, or `null`. The planner falls back to it
  # when the project's lock holds no installer or no build system.
  buildSystems ? null,
  roots ? lock.roots,
  installer ? lock.installer,
  # **A `uv.lock` for the planner to resolve, as a path.** `./uv.nix` gives a
  # value here, and `null` means that `roots` is the whole selection.
  #
  # The Nix half does not read this file. It reads the artefacts of it, which
  # `./uv.nix` enumerates, and it hands the file itself to the planner.
  # `pydd.python._uvlock` resolves it there, with `packaging`, and adds what it
  # resolves to `roots`.
  uvLock ? null,
  # Which packages of `uvLock` to install, and which extras and which
  # dependency groups of each one. `uv.lock` records none of these three
  # decisions, so they are an argument.
  uvSelect ? null,
  # Which extras of each member to enable, and which dependency groups, when
  # `uvSelect` is `null`. `./uv.nix` gives the whole rule at its `extras` and
  # `groups` arguments, and `pydd.python._workspace` holds it. `null` on both is
  # `uv sync` with no flag.
  uvExtras ? null,
  uvGroups ? null,
  # The tree of each package of `uvLock` that builds from a directory, keyed by
  # its normalised name, as `{ source, root, mode, subdirectory }`. **Only the
  # planner knows which of them this target installs**, because only the planner
  # resolves, so every such package of the lock file gets an entry and the
  # resolution picks among them.
  #
  # A git source arrives here too. `uv.nix` fetches the checkout during
  # evaluation and gives it `mode = "wheel"`, because a checkout is a directory
  # and no archive stands between the tree and the wheel.
  uvEditables ? { },
  # The `pyproject.toml` of each member of `uvLock` that lives in a directory
  # of the workspace, keyed by its normalised name, as a store path.
  #
  # **This is what tells a stale lock file from a current one.** `uv.lock`
  # records the declaration of each member in `[package.metadata]`. A project
  # whose `pyproject.toml` has grown a dependency since the last `uv lock`
  # would otherwise build the environment of the old declaration and say
  # nothing. `pydd.python._freshness` makes the comparison, and it gives the
  # rules that it takes from `Lock::satisfies` of uv.
  #
  # An empty set turns the check off, and that is the default. A caller that
  # gives `uvLock` but no project directory has nothing to compare against.
  # `./uv.nix` reads a project, so it names one file for each member.
  # `tests/sealed.nix` gives no `uvLock` at all, so no comparison reaches it.
  uvDeclarations ? { },
  # The `pyproject.toml` of every directory under the workspace, keyed by the
  # path of that directory relative to the root.
  #
  # **`uvDeclarations` cannot find a member that the lock file never saw**,
  # because the lock file is the list of what it compares. A directory that a
  # `[tool.uv.workspace] members` glob names, and that `uv.lock` does not hold,
  # is the other half of a stale lock file. `./workspace.nix` walks the tree to
  # list it, and `pydd.python._workspace` decides which directories are members.
  #
  # An empty set turns that comparison off, and it is the default.
  uvWorkspace ? { },
  # A derivation that runs `uv lock --check` over the project, or `null`.
  #
  # **This is a backstop, and the planner is the check.** `_freshness` compares
  # the lock file with each `pyproject.toml`, and `Lock::satisfies` of uv holds
  # 21 ways for a lock file to be stale. The planner implements the ones that a
  # build reaches. This derivation asks uv itself, so the ones that the planner
  # does not implement stop the build as well.
  #
  # The value is an input of the planner and nothing reads it, so uv answers
  # before any node of the graph builds. `./uv.nix` builds the derivation, and
  # its `uvCheck` argument says what it costs.
  uvCheck ? null,
  # A `poetry.lock` for the planner to read: `{ lock, groups, extras }`.
  # `./poetry.nix` gives a value here.
  poetry ? null,
  # A `pylock.toml` for the planner to read: `{ lock }`. `./pylock.nix` gives
  # a value here.
  pylock ? null,
  # A PEP 723 script for the planner to read: `{ lock, source }`.
  # `./script.nix` gives a value here.
  script ? null,
  # The one member to render as an application, or `null` for the environment.
  #
  # **A virtual environment is not a program.** It carries `bin/python`,
  # `bin/activate` and the console script of every member, so a NixOS
  # configuration that puts one in `environment.systemPackages` shadows the
  # interpreter of the system. An application is the tree of one member,
  # pointing into that same environment, and `scripts/make-application.py`
  # gives the two rules that decide what reaches it.
  #
  # `pyproject.nix` calls this `util.mkApplication`, and it does the same
  # thing: it reads the `$out` of one package as the template of the shape.
  # Here that template is the node that installed the package, which is a
  # store path of this same graph.
  application ? null,
  # Each package to build from its source distribution, even when a wheel fits.
  # This is `tool.uv.no-binary-package`, which uv keeps in `pyproject.toml` and
  # writes into no lock file, so `./uv.nix` reads it from the project.
  noBinary ? [ ],
  # Each package that nixpkgs supplies instead of the index, as
  # `{ pyzmq = pkgs.python3Packages.pyzmq; }`. The planner then makes no wheel
  # node for that name at all.
  #
  # **This is the tier 4 mechanism, and it builds nothing here.** Each
  # derivation is instantiated by this evaluation and handed to the planner as
  # a `.drv` and an output path with the dependency between them discarded, the
  # same way that every artefact of the menu travels. `graph.nix` puts the
  # dependency back for the ones the planner used, so the outer store
  # substitutes the output from a cache. `docs/PACKAGING-FACTS.md` gives the
  # measurement, under "Tier 4, the pre-instantiated menu".
  #
  # **The version is the one that nixpkgs holds, and not the one that the lock
  # file pins.** Nothing reconciles the two, and the planner prints both.
  fromNixpkgs ? { },
  # **The escape hatch** (nixidae/nixidae#787): a function per package name that
  # makes the node of one artefact, as `{ numpy = artifact: drv; }`.
  #
  # `artifact` is `{ package, version, filename, kind, src }`, where `kind` is
  # `wheel` or `sdist` and `src` is the file. The result is a derivation whose
  # output holds `lib/python<version>/site-packages`, as
  # `python.pkgs.buildPythonPackage` writes it, and so it has stdenv,
  # `overrideAttrs` and every setup hook. `null` keeps the planner's own node
  # for that artefact.
  #
  # **The planner still selects.** It chooses the artefact as it would, and
  # names the node that this function made for it. So the version is the one
  # that the lock pins, and every other package is still planned.
  builders ? { },
  # What one package needs to build, by package name, as
  # `{ psutil = [ pkgs.stdenv.cc ]; }`.
  #
  # **A wheel needs nothing here, and a source distribution that holds C code
  # needs a compiler.** `graph.nix` gives every node `coreutils`, `unzip`,
  # `tar`, `gzip` and the interpreter, and no more: a compiler in that list
  # would enter the closure of every pure Python wheel as well.
  #
  # Each entry is a store path, and it reaches the node in four ways:
  #
  #   PATH             `bin` of the entry, in front of the base path.
  #   CPPFLAGS         `-isystem` for `include` of the entry.
  #   LDFLAGS          `-L` for `lib` of the entry.
  #   PKG_CONFIG_PATH  `lib/pkgconfig` of the entry.
  #
  # A directory that the entry does not hold costs nothing, so an entry that
  # is only a program adds no flag that matters.
  #
  # **`NIX_CFLAGS_COMPILE` is not one of them, and `graph.nix` says why.** The
  # wrapper of the compiler reads a name that carries the salt of its
  # platform, and only a setup hook writes that name.
  #
  # **Name each output that the build reads.** A node of this graph runs no
  # `stdenv`, so no setup hook follows `.dev` for you:
  # `[ pkgs.stdenv.cc pkgs.zlib.dev pkgs.zlib ]` gives the compiler, the
  # headers and the library.
  #
  # **The tools reach the node of that package alone.** A build that needs the
  # compiler of another package is a build whose declaration is wrong, and the
  # failure names the package that has to declare it.
  buildTools ? { },
  # Per-package node overrides, by package name, from `./overrides.nix`.
  # `graph.nix` gives the meaning of each key, at `overridesOf`.
  overrides ? { },
  # The edges that each package adds and drops in the walk of the lock, as
  # `{ add, drop }`, by package name. `pydd.python.edits_from` reads them.
  dependencies ? { },
  # The one artefact that replaces every artefact of a package in the menu, by
  # package name: `{ url, hash, filename? }`, a path or a derivation.
  artifactOverrides ? { },
  # Whether an evaluator in a planner may build to import. `false`: a planner
  # writes derivations and builds none (`mkPlanner` of `lib/core`).
  allowImportFromDerivation ? false,
  # The interpreter that the environment runs, and that every install step
  # uses. **This is the whole runtime closure of an environment**, and
  # `docs/PACKAGING-FACTS.md` measures it: 219 MB of the 238 MB that the
  # environment of `projects/pure-app` costs.
  #
  # **Keep the default unless the target is an empty store.** A store that
  # holds `pkgs.python3` already shares 22 of the 23 paths of any override, so
  # a stripped interpreter adds a second CPython rather than replacing the
  # first. The environment then costs 82 MB on that target, against 19 MB with
  # this default. A single-purpose container image is the case where the
  # override wins, and it wins 33% of 238 MB there.
  #
  # `pkgs.python3Minimal` is not a candidate at all: it drops `zlib`, so it
  # cannot open a wheel. A stripped override keeps every optional dependency,
  # and it costs a build of CPython that no binary cache answers:
  #
  #     python = pkgs.python3.override {
  #       stripConfig = true;
  #       stripIdlelib = true;
  #       stripTests = true;
  #       stripTkinter = true;
  #     };
  #
  # **The marker environment comes from this same interpreter**, so the version
  # that the planner plans for and the version that the environment holds are
  # one value and cannot drift.
  python ? pkgs.python3,
  venvName ? "demo-venv",
  # Overrides for the PEP 508 marker environment of the target.
  #
  # `platform_release` and `platform_version` are the two keys that a build
  # cannot know, so both are empty by default. A lock file that carries a
  # marker of either key needs a value here, and `uv2nix` takes the same
  # override for the same reason.
  markers ? { },
  # One entry for each local project to install from its own tree.
  #
  #   src   the project tree, which Nix copies into the store. The backend
  #         reads it, and the environment does not.
  #   root  what an editable install redirects to. A literal path names one
  #         tree forever. A reference to a variable, which is the default,
  #         keeps the environment the same bytes for every tree, so the tree
  #         can change with no rebuild. The other two modes hold the code, so
  #         they need no root and the default is empty.
  #   mode  which of the three forms to build. `editable` is the PEP 660
  #         install that `uv sync` performs, and it is the default. `sdist` is
  #         the archive that `uv build` writes and that an index serves, and a
  #         second node builds the wheel out of it. `wheel` is one PEP 517
  #         `build_wheel` call over the tree, which is what `pip install .`
  #         does and what a git source of `uv.lock` needs.
  #   subdirectory
  #         where the project lives inside `src`, when it is not at the top of
  #         it. `uv.lock` writes this for a repository that holds the project
  #         below its root. The default is empty.
  editables ? { },
}:

let
  # One `fetchurl` per artefact. `fetchurl` is a fixed-output derivation, so
  # instantiating it costs nothing and downloads nothing. The lock file lists
  # 24 artefacts, and this build downloads the ones that the planner picks.
  fetch =
    entry:
    pkgs.fetchurl {
      inherit (entry) url;
      hash = "sha256:${entry.sha256}";
      name = entry.filename;
    };

  # The artefacts, either as the caller gave them or as the lock file names
  # them. Each entry carries `drv`, which is a derivation and not a path, or
  # `path`, which names a file that this machine already holds.
  given =
    if artifacts != null then
      artifacts
    else
      map (entry: (builtins.removeAttrs entry [ "url" ]) // { drv = fetch entry; }) lock.artifacts;

  # **The replacement keeps the version of the lock.** The walk pins
  # `name==<locked version>`, and `choose` drops a row outside that pin, so a
  # version read from the new filename would make the package vanish.
  replacement =
    name: value: rows:
    let
      versions = pkgs.lib.unique (map (entry: entry.version) rows);
      file =
        if pkgs.lib.isDerivation value then
          {
            drv = value;
            filename = value.name;
          }
        else if builtins.isAttrs value then
          let
            filename = value.filename or (baseNameOf (builtins.head (pkgs.lib.splitString "?" value.url)));
          in
          {
            inherit filename;
            drv = pkgs.fetchurl {
              inherit (value) url hash;
              name = filename;
            };
          }
        else
          {
            path = "${value}";
            filename = baseNameOf value;
          };
    in
    if versions == [ ] then
      throw "the artifact override of '${name}' replaces nothing: the lock holds no artefact of it"
    else if builtins.length versions != 1 then
      throw (
        "the artifact override of '${name}' replaces ${toString (builtins.length versions)} versions "
        + "of the lock (${builtins.concatStringsSep ", " versions}), and it names one file"
      )
    else
      file
      // {
        package = (builtins.head rows).package;
        version = builtins.head versions;
        kind = if pkgs.lib.hasSuffix ".whl" file.filename then "wheel" else "sdist";
      }
      // pkgs.lib.optionalAttrs (builtins.all (entry: entry.buildSystems or false) rows) {
        buildSystems = true;
      };

  replaced = pkgs.lib.mapAttrs' (
    name: value: pkgs.lib.nameValuePair (normalise name) { inherit name value; }
  ) artifactOverrides;

  entries =
    let
      kept = builtins.filter (entry: !(replaced ? ${normalise (entry.package or "")})) given;
      rowsOf = name: builtins.filter (entry: normalise (entry.package or "") == name) given;
    in
    kept
    ++ pkgs.lib.mapAttrsToList (
      key: override: replacement override.name override.value (rowsOf key)
    ) replaced;

  # **The two `unsafeDiscard*` builtins are what keep the menu lazy.**
  # `unsafeDiscardOutputDependency` makes the `.drv` an input source of this
  # derivation while the *output* stays unbuilt, so the planner can name the
  # derivation without causing the download. `unsafeDiscardStringContext`
  # gives the planner the output path with no dependency on it.
  #
  # `graph.nix` puts the dependency back, with `builtins.appendContext`, for
  # the artefacts that the planner chooses.
  #
  # **An entry that carries a `path` needs neither builtin, and passes
  # through.** Such an artefact is a file that this machine already holds, so
  # there is nothing to fetch and nothing to defer. `uv.nix` writes one for a
  # source of kind `path`, and `graph.nix` names it with `builtins.storePath`.
  row =
    entry:
    (
      if entry ? path then
        entry
      else
        (builtins.removeAttrs entry [ "drv" ])
        // {
          drv = builtins.unsafeDiscardOutputDependency entry.drv.drvPath;
          out = builtins.unsafeDiscardStringContext entry.drv.outPath;
        }
    )
    // hostRow entry;

  # PEP 503: the name that `packaging.utils.canonicalize_name` gives.
  normalise =
    name:
    pkgs.lib.concatStringsSep "-" (
      builtins.filter (part: builtins.isString part && part != "") (
        builtins.split "[-_.]+" (pkgs.lib.toLower name)
      )
    );
  normalisedBuilders = pkgs.lib.mapAttrs' (
    name: builder: pkgs.lib.nameValuePair (normalise name) builder
  ) builders;

  # The node that `builders` makes for one artefact, as `row` gives a fetch.
  hostRow =
    entry:
    let
      builder = normalisedBuilders.${normalise entry.package} or null;
      made =
        if builder == null then
          null
        else
          builder {
            inherit (entry) package version filename kind;
            src = entry.path or entry.drv;
          };
    in
    pkgs.lib.optionalAttrs (entry ? package && made != null) {
      hostDrv = builtins.unsafeDiscardOutputDependency made.drvPath;
      hostOut = builtins.unsafeDiscardStringContext made.outPath;
    };

  # One entry for each package that nixpkgs supplies. It is the same shape that
  # `row` gives an artefact, plus the version that nixpkgs holds, which the
  # planner prints beside the version that the lock file pinned.
  nixpkgsRows = builtins.mapAttrs (
    _: drv: (row { inherit drv; }) // { inherit (drv) version; }
  ) fromNixpkgs;

  scriptDir = ../../src/pydd/python/scripts;

  inherit (pydd) render protocol;

  # **Every planner of this graph, the outer one and one for each source
  # distribution.** `packaging` and `pydd.python` reach the planner and never
  # the environment that comes out.
  mkPythonPlanner =
    {
      name,
      main,
      env,
    }:
    pydd.mkPlanner {
      inherit name allowImportFromDerivation;
      env = env // {
        PYDD_RENDER = render;
      };
      plan = pkgs.writeText "plan.py" "import sys\nfrom pydd.python.${main} import main\nsys.exit(main())\n";
      pythonPath = [ pkgs.python3Packages.packaging ];
      # The ecosystem, and the one mode that turns this planner's plan into
      # derivations (`pydd.modes`).
      modules = [
        (pkgs.lib.fileset.difference (pkgs.lib.fileset.fileFilter (
          file: file.hasExt "py"
        ) ../../src/pydd/python) scriptDir)
        # `_hook.py` makes the backend's venv with it.
        (scriptDir + "/make-venv.py")
        ../../src/pydd/modes/__init__.py
        (
          {
            nix = ../../src/pydd/modes/evaluate;
            nix-cli = ../../src/pydd/modes/nix_cli;
            python = ../../src/pydd/modes/render;
          }
          .${render}
        )
      ]
      ++ pkgs.lib.optional (protocol == "out") ../../src/pydd/modes/out;
    };

  #: How many levels of planner a chain of source distributions may use. A
  #: planner of a distribution resolves a backend, and that backend may be a
  #: source distribution which needs a planner of its own. The recursion has to
  #: stop somewhere, so it stops here, and `graph.nix` names this constant when
  #: a chain runs past it.
  #:
  #: `tests/functional/dyn-drv/eval-submit.sh` of the patched Nix runs three
  #: levels, which is what this value allows.
  #:
  #: **The bound is tested at both edges.** `tests/fixtures/default.nix` holds
  #: a chain of four source distributions, and the two roots of
  #: `tests/sealed.nix` differ in nothing but the name: `link-a` uses three
  #: planners and builds, and `link-0` needs a fourth and makes `graph.nix`
  #: throw. `docs/NIX-FACTS.md` records the message and the level that it
  #: comes from.
  sdistPlannerDepth = 3;

  # **A caller that names no tool gets no key.** `Plan.tools_for` follows the
  # same rule for a node, and the reason is the same: the document is the
  # input of a derivation, so an empty key is a byte that every planner of
  # every project carries for nothing.
  #
  # Each entry travels as `built` gives it, the way a tool does.
  buildToolPaths = pkgs.lib.optionalAttrs (buildTools != { }) {
    buildTools = pkgs.lib.mapAttrs (_: map built) buildTools;
  };

  # A patch travels as a tool does, so a `fetchpatch` output names its `.drv`.
  overridePaths = pkgs.lib.optionalAttrs (overrides != { }) {
    overrides = pkgs.lib.mapAttrs (
      _: override:
      override // pkgs.lib.optionalAttrs (override ? patches) { patches = map built override.patches; }
    ) overrides;
  };

  # **A tool travels as an artefact does: the `.drv`, and the path of one
  # output.** A node then names the tool's derivation as an input, as a node
  # that the host evaluates from `graph.nix` names it. With a plain store path
  # instead, the two would be different derivations. The planner puts the
  # dependency back (`pydd.modes.evaluate._document.document_value`). A store path that no derivation
  # makes, such as a source, travels as `{ out }`.
  built =
    value:
    if pkgs.lib.isDerivation value then
      {
        drv = builtins.unsafeDiscardOutputDependency value.drvPath;
        out = builtins.unsafeDiscardStringContext value.outPath;
        output = value.outputName or "out";
      }
    else
      { out = "${value}"; };

  tools = {
    inherit (pkgs)
      bash
      coreutils
      unzip
      gnutar
      gzip
      patchelf
      ;
    # Only a node with `patches` reads this.
    patch = pkgs.gnupatch;
    # What a source with native code builds with, when nothing names a tool
    # for it (`pydd.python.has_native_sources`).
    compiler = pkgs.stdenv.cc;
    inherit python;
    # `patchelf` and the three below reach only a node that installs a
    # binary wheel. `pkgs.autoPatchelfHook` is a setup hook, and a graph
    # node runs no setup hook. `pkgs.auto-patchelf` is the program that the
    # hook calls.
    autoPatchelf = pkgs.auto-patchelf;
    bintools = pkgs.stdenv.cc.bintools;
    # **The libraries a manylinux wheel may assume a system has**, PEP 599:
    # libgcc_s and libstdc++, but also zlib, X11, GL, glib and expat. pillow
    # needs `libz.so.1`. A list, so each directory stays apart, and a RUNPATH
    # names only the library that a binary uses.
    manylinux = map pkgs.lib.getLib pkgs.pythonManylinuxPackages.manylinux2014;
  };

  scripts = {
    # One store path for each script, so an edit to one rebuilds only the
    # nodes that run it.
    unpackWheel = "${scriptDir + "/unpack-wheel.sh"}";
    installWheel = "${scriptDir + "/install-wheel.sh"}";
    buildSdist = "${scriptDir + "/build-sdist.sh"}";
    buildLocal = "${scriptDir + "/build-local.py"}";
    makeVenv = "${scriptDir + "/make-venv.py"}";
    runInstaller = "${scriptDir + "/run-installer.py"}";
    makeApplication = "${scriptDir + "/make-application.py"}";
    fixupElf = "${scriptDir + "/fixup-elf.py"}";
  };

  # **A mode adds to the inputs what it needs.** `../modes/out.nix` gives each
  # planner the nodes that the host made, and an application the planner of
  # its environment. A mode on `builder-rpc-v0` adds nothing.
  mode =
    if protocol == "out" then
      import ../modes/out.nix {
        inherit
          pkgs
          python
          tools
          scripts
          entries
          installer
          mkPythonPlanner
          plannerEnv
          menu
          venvName
          application
          plannerRow
          localPlanned
          ;
      }
    else
      {
        menu = { };
        inputs = { };
      };

  # **stdenv as data.** The template travels as JSON, so its strings lose
  # their context. Each path of that context is an input of every planner
  # instead: a `.drv` with no dependency on its output, as a fetch of the
  # menu, and a source as itself. So a planner can write a node that names
  # stdenv, and stdenv is not built for the planner.
  template = import ./stdenv-template.nix { pkgs = pydd.stdenv; };
  stdenvTemplate = pkgs.lib.optionalAttrs (pydd.stdenv != null) {
    stdenvTemplate = template.encoded;
  };
  stdenvInputs =
    let
      context = builtins.getContext (builtins.toJSON template.raw);
    in
    pkgs.lib.optionalAttrs (pydd.stdenv != null) {
      PYDD_STDENV_INPUTS = toString (
        pkgs.lib.mapAttrsToList (
          path: kind:
          if kind ? outputs then
            builtins.unsafeDiscardOutputDependency (builtins.appendContext path { ${path}.allOutputs = true; })
          else
            builtins.appendContext path { ${path}.path = true; }
        ) context
      );
    };

  baseInputs =
    mode.menu
    // buildToolPaths
    // overridePaths
    // pkgs.lib.optionalAttrs (dependencies != { }) { inherit dependencies; }
    // stdenvTemplate
    // pkgs.lib.optionalAttrs (buildSystems != null) { buildSystems = "${buildSystems}"; }
    // {
      inherit
        roots
        installer
        noBinary
        application
        ;
      fromNixpkgs = nixpkgsRows;
      artifacts = map row entries;
      tools = builtins.mapAttrs (_: tool: if builtins.isList tool then map built tool else built tool) tools;
      inherit scripts;
      interpreter = { inherit (python) executable sitePackages; };
      # The planner reads the `pyproject.toml` of each tree below, and resolves
      # the backend that the tree asks for against the same lock file.
      editables = pkgs.lib.mapAttrs (_: spec: {
        source = "${spec.src}";
        # **Only an editable install has a root.** The other two forms hold the
        # code, so nothing redirects to a tree of the user and the value is empty.
        root = spec.root or "";
        mode = spec.mode or "editable";
        subdirectory = spec.subdirectory or "";
      }) editables;
    }
    // pkgs.lib.optionalAttrs (uvLock != null) {
      # The lock file itself, as a store path. `builtins.toJSON` keeps the string
      # context, so the file becomes an input source of every planner and is
      # present in the sandbox.
      uv = {
        lock = "${uvLock}";
        select = uvSelect;
        extras = uvExtras;
        groups = uvGroups;
        editables = uvEditables;
        declarations = uvDeclarations;
        workspace = uvWorkspace;
      };
    }
    // pkgs.lib.optionalAttrs (poetry != null) {
      # `{ lock, groups, extras }`, from `./poetry.nix`. The lock file travels
      # as a store path, as `uvLock` does.
      inherit poetry;
    }
    // pkgs.lib.optionalAttrs (pylock != null) {
      # `{ lock }`, from `./pylock.nix`, as a store path.
      inherit pylock;
    }
    // pkgs.lib.optionalAttrs (script != null) {
      # `{ lock, source }`, from `./script.nix`, as store paths.
      inherit script;
    };

  # What every planner reads about the target it plans for. The planner runs on
  # one machine and plans for another, so these are values.
  #
  # **The marker environment carries every key of PEP 508, and it has to.**
  # `Marker.evaluate` of `packaging` fills a key that the caller leaves out
  # from the interpreter that is running, so a key that this attrset does not
  # give would describe the machine that plans. `docs/PACKAGING-FACTS.md` holds
  # the measurement, and `pydd.python.target_from_environ` refuses a short set.
  inherit (pkgs.stdenv) hostPlatform;

  #: The two keys that `platform.release()` and `platform.version()` answer,
  #: which is the kernel of the machine that runs the environment. No build
  #: knows that, so both are empty here. `pyproject-nix` does the same, in
  #: `lib/pep508.nix`, and it gives the same reason. A caller that needs a
  #: marker of either key overrides `markers`.
  targetMarkers = {
    implementation_name = python.implementation;
    # PyPy's `version` is its own release, as `7.3.20`, which is what
    # `sys.implementation.version` says too.
    implementation_version = python.version;
    os_name = "posix";
    platform_machine = hostPlatform.parsed.cpu.name;
    platform_python_implementation = if python.implementation == "cpython" then "CPython" else "PyPy";
    platform_release = "";
    platform_system = if hostPlatform.isDarwin then "Darwin" else "Linux";
    platform_version = "";
    # nixpkgs knows no patch level of the language that PyPy implements. `.99`
    # assumes that PyPy tracks the newest bugfix release; `markers` overrides it.
    python_full_version =
      if python.implementation == "cpython" then python.version else "${python.pythonVersion}.99";
    python_version = python.pythonVersion;
    sys_platform = if hostPlatform.isDarwin then "darwin" else "linux";
  };

  targetEnv = {
    PYDD_TARGET = builtins.toJSON {
      pythonVersion = python.pythonVersion;
      # `cp313t` for a free-threaded build. `abi3` and `none` are left to
      # `packaging`, which adds them when the ABI allows.
      abis = builtins.filter (tag: tag != "abi3" && tag != "none") python.pythonABITags;
      # The platform of a wheel tag, which PEP 425 spells differently from
      # `platform_machine`: Apple names aarch64 `arm64`.
      platform =
        if hostPlatform.isDarwin then
          "macosx_${hostPlatform.darwinArch}"
        else
          "linux_${hostPlatform.parsed.cpu.name}";
      # The oldest macOS that the target's binaries run on.
      macosVersion = if hostPlatform.isDarwin then hostPlatform.darwinMinVersion else "";
      # The newest manylinux or musllinux tag that runs here.
      libc = hostPlatform.libc;
      libcVersion = if hostPlatform.isLinux then pkgs.stdenv.cc.libc.version else "";
      markers = targetMarkers // markers;
    };
  };

  plannerEnv =
    stdenvInputs
    // pkgs.lib.optionalAttrs (render == "nix" || render == "nix-cli") {
      PYDD_GRAPH_NIX = "${./graph.nix}";
    }
    // pkgs.lib.optionalAttrs (pydd.nixpkgs != null) {
      # **`"${pkgs.path}"` copies nixpkgs a second time**, under a new name,
      # because a path value is copied on interpolation even when it lies in
      # the store. `builtins.storePath` names the path that is there. Pure
      # evaluation has no `storePath`, and `currentSystem` says which one
      # this is.
      PYDD_NIXPKGS =
        if builtins ? currentSystem then builtins.storePath pydd.nixpkgs else "${pydd.nixpkgs}";
      PYDD_NIXPKGS_OVERLAYS = builtins.toJSON (map (overlay: "${overlay}") pydd.nixpkgsOverlays);
      PYDD_NIXPKGS_CONFIG = builtins.toJSON pydd.nixpkgsConfig;
    }
    // pkgs.lib.optionalAttrs (render == "nix-cli") {
      PYDD_NIX_EVAL = "${../modes/nix-eval.nix}";
    }
    // targetEnv;

  # **The planner of one source distribution, instantiated here and not
  # inside the sandbox.** A planner cannot read a store path that the planner
  # itself added, so `builtins.outputOf` on a derivation that a planner wrote
  # fails with "opening file ...: No such file or directory". Instantiating it
  # out here makes the `.drv` an input source of the outer planner, so the read
  # succeeds. `docs/NIX-FACTS.md` holds the measurement.
  #
  # **Each archive takes two planners of the same program** (nixidae/nixidae#847).
  # The `backend` role submits the backend's environment. The `build` role
  # takes that environment built, asks the backend's
  # `get_requires_for_build_wheel`, and submits the build with the answer on
  # its path. Only a built backend can answer, and a planner can depend on the
  # output of another only when both are instantiated here.
  sdistPlanner =
    depth: entry:
    let
      backend = sdistPlannerAs depth entry "backend" "${entry.package}-${entry.version}-backend" { };
    in
    (sdistPlannerAs depth entry "build" "${entry.package}-${entry.version}" (
      hookEnv entry.package backend
    )).planner;

  # What the `build` role of a planner needs to ask the backend that its
  # `backend` role submitted.
  hookEnv = package: backend: {
    PYDD_BACKEND = backend.out;
    # The interpreter of the target runs the hook, as it runs the build.
    PYDD_PYTHON = "${python}/bin/${python.executable}";
    # **The hook sees the tools that the build sees.** mesonpy asks for
    # `ninja` and `patchelf`, and scikit-build-core for `cmake` and `ninja`,
    # only when `PATH` lacks the program, and no lock holds those wheels.
    PYDD_HOOK_PATH = builtins.concatStringsSep ":" (
      map (tool: "${tool}/bin") (
        let
          roots = buildTools.${package} or [ ];
        in
        roots ++ pkgs.lib.optional (roots != [ ]) pkgs.patchelf
      )
    );
  };

  sdistPlannerAs =
    depth: entry: role: name: roleEnv:
    mkPythonPlanner {
      # Nix requires the planner to carry the name of what it submits.
      inherit name;
      main = "_sdist_planner";
      env =
        plannerEnv
        // roleEnv
        // {
          PYDD_ROLE = role;

          # **The archive is a real dependency of this derivation.** That is the
          # whole point: this planner reads a file that no evaluation can read,
          # and Nix downloads it before the planner runs.
          #
          # An archive of a `path` source needs no download, and the string that
          # `uv.nix` wrote already carries the context of the file. Nix makes
          # that file an input source of this planner in the same way.
          PYDD_SDIST = if entry ? path then entry.path else entry.drv;

          # **The menu is the same for every planner of one level, so it is
          # serialised once for that level and not once for each planner.**
          # `levelJson` is a list, and a list is a memo, so `builtins.toJSON`
          # runs one time for each level. It ran one time for each planner while
          # the row below was part of this document, and the lock file of the
          # benchmark then paid 27.9 MB of JSON for each level.
          # `docs/PACKAGING-FACTS.md` holds both measurements.
          PYDD_INPUTS = builtins.elemAt levelJson (depth - 1);

          # **A URL source names where its project lives inside the archive.**
          # `uv.nix` puts `subdirectory` on the artefact, and the planner of this
          # distribution reads the declaration there. An artefact of the index
          # carries no such key.
          #
          # This is the one part that differs between two planners of one level,
          # and it is a few hundred bytes.
          PYDD_SDIST_ROW = builtins.toJSON (
            {
              inherit (entry) package version filename;
            }
            // pkgs.lib.optionalAttrs (entry ? subdirectory) { inherit (entry) subdirectory; }
          );
        };
    };

  # **The levels are a list, and not a recursive function, because a list is a
  # memo.** Nix evaluates each element of a list at most once, and it
  # re-evaluates the body of a function at every call. `sdistPlanner` needs the
  # level below it, and there is one planner for each source distribution, so a
  # function costs O(n ^ depth) and this list costs O(n * depth).
  #
  # `docs/NIX-FACTS.md` holds the measurement of both, and the lock files that
  # this lab measured hold up to 98 source distributions. The recursive form
  # did not reach such a lock file.
  levels = builtins.genList inputsAt (sdistPlannerDepth + 1);

  # **The same memo, over the text of each level.** Every planner of one level
  # reads the same menu, so the JSON of that menu is one string for the level.
  # A `builtins.toJSON` inside `sdistPlanner` would run one time for each
  # planner instead, over a document of a quarter of a megabyte.
  levelJson = map builtins.toJSON levels;

  # The inputs, with a planner for each source distribution, to `depth`
  # levels. This maps over the lock file and not over `baseInputs.artifacts`,
  # because a planner needs the `url` that `row` removes.
  inputsAt =
    depth:
    baseInputs
    // {
      artifacts = map (
        entry:
        let
          base = row entry;
        in
        if entry.kind == "sdist" && depth > 0 then
          let
            planner = sdistPlanner depth entry;
          in
          base
          // {
            # The same bargain as `row` strikes for a fetch: the `.drv` is an
            # input source, so the file is present and the planner has not run.
            plannerDrv = builtins.unsafeDiscardOutputDependency planner.drvPath;
            plannerOut = builtins.unsafeDiscardStringContext planner.outPath;
          }
        else
          base
      ) entries;
    };

  menu = builtins.elemAt levels sdistPlannerDepth;

  # A planner, as a node of another graph names it: the `.drv` as an input,
  # and the output path with no dependency, as `row` gives a fetch.
  plannerRow = planner: {
    plannerDrv = builtins.unsafeDiscardOutputDependency planner.drvPath;
    plannerOut = builtins.unsafeDiscardStringContext planner.outPath;
  };

  # **A local project is the root of a planner of its own.** Its backend is
  # declared in its tree, so no evaluation knows its node. Each such planner
  # runs the same selection as the environment and submits only the node of
  # one project, `PYDD_ONLY`. A project built from its own sdist takes two:
  # the archive, and the wheel out of it, which names the first.
  #
  # **Each takes a `backend` planner too, as a source distribution does**, so
  # the `build` role can ask the backend's `get_requires_for_build_*`. One
  # `backend` planner serves every form of a project: the forms share a path.
  #
  # A planner of a local project gets no planner of another local project: two
  # members that need each other would make two planners that need each other.
  #
  # `planned` travels apart from the menu, so `PYDD_INPUTS` stays the one
  # string of the level that `levelJson` serialised.
  localSuffix = {
    editable = "editable";
    wheel = "from-tree";
    sdist = "sdist";
    from-sdist = "from-sdist";
  };
  localPlannerAs =
    project: form: role: name: planned: roleEnv:
    mkPythonPlanner {
      # Nix requires a planner to carry the name of what it submits.
      inherit name;
      main = "_environment";
      env =
        plannerEnv
        // roleEnv
        // {
          PYDD_ROLE = role;
          PYDD_INPUTS = builtins.elemAt levelJson sdistPlannerDepth;
          PYDD_PLANNED = builtins.toJSON planned;
          PYDD_VENV_NAME = venvName;
          PYDD_ONLY = "${project}:${form}";
        };
    };
  localPlanned = pkgs.lib.concatMapAttrs (
    project: spec:
    let
      form = spec.mode or "editable";
      backend = localPlannerAs project form "backend" "${project}-backend" { } { };
      build =
        form: planned:
        plannerRow
          (localPlannerAs project form "build" "${project}-${localSuffix.${form}}" planned (
            hookEnv project backend
          )).planner;
    in
    if form == "sdist" then
      let
        archive = {
          "${project}:sdist" = build "sdist" { };
        };
      in
      archive // { "${project}:from-sdist" = build "from-sdist" archive; }
    else
      { "${project}:${form}" = build form { }; }
  ) (editables // uvEditables);

  inputs = menu // mode.inputs;
in
mkPythonPlanner {
  # The root that this planner submits is `<venvName>.drv`, and Nix requires
  # the planner to carry the name of what it submits.
  name = venvName;
  main = "_environment";
  env =
    plannerEnv
    // {
      # **Every store path that the planner names travels in this string.**
      # `builtins.toJSON` keeps the string context of what it serialises, so
      # each `.drv` of the menu, each tool and each script becomes an input
      # source of this derivation. That is what makes them present in the
      # sandbox.
      PYDD_INPUTS = builtins.toJSON inputs;
      PYDD_PLANNED = builtins.toJSON localPlanned;

      PYDD_VENV_NAME = venvName;
    }
    // pkgs.lib.optionalAttrs (uvCheck != null) {
      # **Nothing reads this, and that is the whole design.** A store path in
      # an environment variable of a derivation is an input of that
      # derivation, so Nix builds `uvCheck` before it runs this planner, and a
      # refusal of uv stops the build here.
      PYDD_UV_CHECK = "${uvCheck}";
    };
}
