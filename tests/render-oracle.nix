# What `lib/python/graph.nix` makes of a plan document that holds every node
# kind, for `test_render.py` to hold `pydd.modes.render` to.
#
# The document carries the context that `pydd.modes.evaluate._document.document_value` attaches in a
# planner. `nix eval --read-only` computes each `.drvPath` and writes nothing.
let
  system = "x86_64-linux";
  names =
    list: f:
    builtins.listToAttrs (
      map (name: {
        inherit name;
        value = f name;
      }) list
    );

  # A store path with plain context, as a tool or a tree reaches a planner.
  source = name: builtins.toFile name name;

  # An artefact of the menu: the `.drv` with no context, and the output with
  # the context of its derivation.
  fetch =
    filename:
    let
      drv = derivation {
        name = filename;
        inherit system;
        builder = "/bin/sh";
        outputHashMode = "flat";
        outputHashAlgo = "sha256";
        outputHash = builtins.hashString "sha256" filename;
      };
    in
    {
      inherit filename;
      drv = builtins.unsafeDiscardStringContext drv.drvPath;
      out = "${drv}";
    };

  # The planner of one source distribution: a floating text output.
  planner = derivation {
    name = "flit-core-3.12.0.drv";
    inherit system;
    builder = "/bin/sh";
    __contentAddressed = true;
    outputHashMode = "text";
    outputHashAlgo = "sha256";
  };

  # The planner of a local project, which writes that project's node.
  localPlanner = derivation {
    name = "tool-editable.drv";
    inherit system;
    builder = "/bin/sh";
    __contentAddressed = true;
    outputHashMode = "text";
    outputHashAlgo = "sha256";
  };

  nixpkgs = derivation {
    name = "pyzmq-26.0.0";
    inherit system;
    builder = "/bin/sh";
  };

  tools = names [
    "bash"
    "coreutils"
    "unzip"
    "gnutar"
    "gzip"
    "python"
    "patchelf"
    "autoPatchelf"
    "bintools"
    "manylinux"
    "patch"
  ] tool;

  scripts = names [
    "unpackWheel"
    "installWheel"
    "buildSdist"
    "buildLocal"
    "makeVenv"
    "makeApplication"
    "fixupElf"
  ] (name: source "script-${name}");

  # A tool as `planner.nix` hands one over: an output of a derivation, or a
  # source as `{ out }`.
  built = value: {
    drv = builtins.unsafeDiscardStringContext value.drvPath;
    out = "${value}";
    output = value.outputName;
  };
  package =
    name:
    derivation {
      inherit name system;
      builder = "/bin/sh";
      outputs = [
        "out"
        "lib"
      ];
    };
  tool =
    name:
    if name == "manylinux" then
      [
        (built (package "gcc").lib)
        (built (package "zlib").out)
      ]
    else if name == "patchelf" then
      { out = source "tool-patchelf"; }
    else
      built (package "tool-${name}");

  compiler = [
    (built (package "cc"))
    { out = source "zlib-dev"; }
  ];

  nodes = [
    {
      name = "installer-bootstrap";
      kind = "unpack";
      platform = false;
      artifact = fetch "installer-0.7.0-py3-none-any.whl";
    }
    {
      name = "rich";
      kind = "wheel";
      platform = false;
      artifact = fetch "rich-14.0.0-py3-none-any.whl";
    }
    {
      name = "psutil";
      kind = "wheel";
      platform = true;
      artifact = fetch "psutil-7.0.0-cp36-abi3-manylinux_2_17_x86_64.whl";
    }
    {
      name = "flit-sdist";
      kind = "sdist";
      platform = false;
      artifact = (fetch "flit_core-3.12.0.tar.gz") // {
        plannerDrv = builtins.unsafeDiscardStringContext planner.drvPath;
        plannerOut = "${planner}";
      };
    }
    {
      name = "idna";
      kind = "sdist-build";
      platform = false;
      artifact = (fetch "idna-3.10.tar.gz") // {
        subdirectory = "sub";
      };
      backend = "flit_core.buildapi";
      backendPath = [ "flit-sdist" ];
      buildTools = compiler;
    }
    {
      # Every key of a node override, as `pydd.python._plan.overrides_from`
      # hands it over: `configSettings` already JSON.
      name = "numpy";
      kind = "sdist-build";
      platform = false;
      artifact = fetch "numpy-2.3.0.tar.gz";
      backend = "mesonpy";
      backendPath = [ "flit-sdist" ];
      buildTools = compiler;
      overrides = {
        env = {
          NPY_DISABLE_SVML = "1";
          PATH = "/replaced";
        };
        configSettings = ''{"setup-args":["-Dblas=openblas","-Dlapack=openblas"]}'';
        patches = [
          { out = source "numpy.patch"; }
          (built (package "fetched-patch"))
        ];
        preBuild = "export FOO=1";
        postInstall = "rm -r $out/bin";
        fixup = false;
      };
    }
    {
      name = "app-editable";
      kind = "editable";
      platform = false;
      source = source "app-tree";
      root = "$PYDD_ROOT";
      backend = "flit_core.buildapi";
      backendPath = [ "flit-sdist" ];
      subdirectory = "";
      buildTools = compiler;
    }
    {
      name = "git-from-tree";
      kind = "local-wheel";
      platform = false;
      source = source "git-tree";
      backend = "setuptools.build_meta";
      backendPath = [ ];
      subdirectory = "python";
    }
    {
      name = "lib-sdist";
      kind = "local-sdist";
      platform = false;
      source = source "lib-tree";
      backend = "flit_core.buildapi";
      backendPath = [ "flit-sdist" ];
      subdirectory = "";
    }
    {
      name = "lib-from-sdist";
      kind = "sdist-build";
      platform = false;
      from = "lib-sdist";
      backend = "flit_core.buildapi";
      backendPath = [ "flit-sdist" ];
    }
    {
      name = "tool-editable";
      kind = "planned";
      platform = false;
      artifact = {
        plannerDrv = builtins.unsafeDiscardStringContext localPlanner.drvPath;
        plannerOut = "${localPlanner}";
      };
    }
    {
      name = "pyzmq-26.0.0-nixpkgs";
      kind = "nixpkgs";
      platform = false;
      artifact = {
        package = "pyzmq";
        version = "26.0.0";
        drv = builtins.unsafeDiscardStringContext nixpkgs.drvPath;
        out = "${nixpkgs}";
      };
    }
    {
      name = "idna-3.10-host";
      kind = "host";
      platform = false;
      artifact = {
        package = "idna";
        version = "3.10";
        drv = builtins.unsafeDiscardStringContext nixpkgs.drvPath;
        out = "${nixpkgs}";
      };
    }
    {
      name = "app-env";
      kind = "venv";
      platform = false;
      members = [
        "rich"
        "psutil"
        "idna"
        "numpy"
        "app-editable"
        "git-from-tree"
        "lib-from-sdist"
        "tool-editable"
        "pyzmq-26.0.0-nixpkgs"
        "idna-3.10-host"
      ];
      installed = [
        "rich==14.0.0"
        "psutil==7.0.0"
      ];
    }
    {
      name = "app";
      kind = "application";
      platform = false;
      venv = "app-env";
      bases = [ "app-editable" ];
    }
    {
      name = "idna-backend";
      kind = "backend";
      platform = false;
      backendPath = [ "flit-sdist" ];
    }
  ];

  document = {
    inherit
      system
      tools
      scripts
      nodes
      ;
    interpreter = {
      executable = "python3.13";
      sitePackages = "lib/python3.13/site-packages";
    };
    installer = "installer-bootstrap";
    root = "app";
  };

  # stdenv as data, with each kind of value that `derivation` coerces: a
  # list, a boolean, a null that `__ignoreNulls` drops, and a string that
  # carries an output's context.
  stdenv = {
    __ignoreNulls = true;
    __structuredAttrs = false;
    args = [
      "-e"
      (source "source-stdenv.sh")
      (source "default-builder.sh")
    ];
    builder = "${package "stdenv-bash"}/bin/bash";
    stdenv = "${package "stdenv"}";
    cmakeFlags = [ ];
    doCheck = false;
    strictDeps = false;
    userHook = null;
    outputs = [ "out" ];
    inherit system;
    nativeBuildInputs = [ "${package "patchelf-hook"}" ];
  };
  sourceNodes = builtins.filter (
    node:
    builtins.elem node.kind [
      "editable"
      "local-wheel"
      "local-sdist"
      "sdist-build"
    ]
  ) nodes;

  graph = import ../lib/python/graph.nix;
  pathOf = value: if builtins.isAttrs value then value.drvPath else value;
in
{
  inherit document;
  stdenvDocument = document // {
    stdenv = import ../lib/python/carry.nix stdenv;
  };
  expectedStdenv = builtins.listToAttrs (
    map (node: {
      inherit (node) name;
      value = (graph (document // { inherit stdenv; root = node.name; })).drvPath;
    }) sourceNodes
  );
  expected = builtins.listToAttrs (
    map (node: {
      inherit (node) name;
      value = pathOf (graph (document // { root = node.name; }));
    }) nodes
  );
}
