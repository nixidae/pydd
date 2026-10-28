{
  lib,
  bash,
  python3,
  nixVersions,
  linkFarmFromDrvs,
  runCommand,
  nixvenv,
}:

let
  inherit (lib) fileset;

  # Only `.py` files, so a stray `__pycache__` cannot change a planner.
  # The core is `pydd` and `pydd.core`. An ecosystem and a mode add their own
  # subpackage through `modules`, so an edit to one does not rebuild the
  # planners of the others.
  core = fileset.intersection (fileset.fileFilter (file: file.hasExt "py") ../../src/pydd) (
    fileset.unions [
      ../../src/pydd/__init__.py
      ../../src/pydd/core
    ]
  );
  pyddSrc =
    modules:
    fileset.toSource {
      root = ../../src;
      fileset = fileset.unions ([ core ] ++ modules);
    };

  # The source as a package, so a venv holds it beside what it imports.
  pyddPackage =
    python: modules:
    python.pkgs.toPythonModule (
      runCommand "pydd-planner-source" { } ''
        mkdir -p $out/${python.sitePackages}
        ln -s ${pyddSrc modules}/pydd $out/${python.sitePackages}/pydd
      ''
    );
in
{
  /**
    Discard every dependency a menu would carry, but keep the `.drv`.

    A planner may then name the derivation as an input of a node it writes,
    and Nix builds it only when a node does. A candidate the planner does
    not pick is never built.

    # Inputs

    `drv`
    : A derivation.

    `outputs`
    : The outputs to give the planner the paths of.
  */
  candidate =
    drv:
    {
      outputs ? drv.outputs or [ "out" ],
    }:
    {
      drv = builtins.unsafeDiscardOutputDependency drv.drvPath;
      outputs = lib.genAttrs outputs (output: builtins.unsafeDiscardStringContext drv.${output}.outPath);
    };

  /**
    A `builder-rpc-v0` derivation that runs a Python planner.

    The planner writes a graph and submits its root, a derivation named
    `name`. Nix asks the planner to carry the name of what it submits, so
    the planner itself is named `name.drv`.

    Returns `{ planner, out }`, where `out` is the root's output, through
    `builtins.outputOf`.

    # Inputs

    `name`
    : The name of the root derivation the planner submits.

    `plan`
    : The planner, a Python file.

    `menu`
    : Anything `builtins.toJSON` takes. The planner reads it as
      `Planner.menu`. Every store path in it becomes an input of the
      planner, so a node it writes may read that path.

    `writer`
    : `"cli"` writes through `nix derivation add`. `"huggorm"` writes in
      process, and needs `huggorm`. `"drv"` writes through `pydd-drv`, and
      the planner then carries no `nix`.

    `pyddDrv`
    : The `pydd-drv` package, for `writer = "drv"`.

    `huggorm`
    : A huggorm package built against a Nix with `builder-rpc-v0`.

    `pythonPath`
    : Packages the plan imports, such as `packaging`.

    `modules`
    : Filesets under `src/` that the plan imports beside the core of
      `pydd`, such as `src/pydd/python`.

    `env`
    : More environment variables of the planner. A store path in a value
      becomes an input of the planner.

    `protocol`
    : `"rpc"` writes the graph through `builder-rpc-v0`, on Nix master.
      `"recursive"` writes it through the daemon socket of recursive-nix,
      on a release, and the root as ATerm to `$out`.
      `"out"` is for released Nix: the planner writes one derivation, as
      ATerm, to `$out`, and every other derivation it names must be an
      input of the planner. It needs the huggorm writer, which stages the
      ATerm in memory.

    `allowImportFromDerivation`
    : Whether an evaluator in the planner may build to import, as Nix's
      `allow-import-from-derivation`. `false` by default: a planner writes
      derivations, and builds none. The result's `overrideAttrs` changes it
      on one planner, as any attribute of the planner.
  */
  mkPlanner =
    {
      name,
      plan,
      menu ? null,
      writer ? "cli",
      huggorm ? null,
      nix ? nixVersions.git,
      pyddDrv ? null,
      python ? if writer == "huggorm" then huggorm.pythonModule else python3,
      pythonPath ? [ ],
      modules ? [ ],
      env ? { },
      system ? python.stdenv.hostPlatform.system,
      protocol ? "rpc",
      allowImportFromDerivation ? false,
    }:
    assert lib.assertOneOf "writer" writer [
      "cli"
      "huggorm"
      "drv"
    ];
    assert lib.assertMsg (
      writer == "drv" -> pyddDrv != null
    ) "mkPlanner: writer = \"drv\" needs pyddDrv";
    assert lib.assertOneOf "protocol" protocol [
      "rpc"
      "out"
      "recursive"
    ];
    assert lib.assertMsg (
      protocol == "out" -> writer == "huggorm"
    ) "mkPlanner: protocol = \"out\" stages the ATerm in huggorm, so it needs writer = \"huggorm\"";
    assert lib.assertMsg (
      writer == "huggorm" -> huggorm != null
    ) "mkPlanner: writer = \"huggorm\" needs huggorm";
    let
      # A venv, not PYTHONPATH: `sys.executable` then imports what the plan
      # imports, in the planner and in what it starts.
      venv = nixvenv {
        inherit python;
        packages =
          _: [ (pyddPackage python modules) ] ++ lib.optional (writer == "huggorm") huggorm ++ pythonPath;
      };
      attributes =
        env
        // {
          inherit allowImportFromDerivation;
          name = "${name}.drv";
          inherit system;
          builder = "${bash}/bin/bash";
          # The full path: CPython finds its prefix from argv[0], and Nix runs a
          # builder with only the basename there.
          args = [
            "-c"
            "exec ${venv}/bin/python ${plan}"
          ];

          requiredSystemFeatures =
            lib.optional (protocol == "rpc") "builder-rpc-v0"
            ++ lib.optional (protocol == "recursive") "recursive-nix";
          # The submitted object is a derivation, and a derivation is
          # text-ingested.
          __contentAddressed = true;
          outputHashMode = "text";
          outputHashAlgo = "sha256";

          PATH = lib.makeBinPath [ (if writer == "drv" then pyddDrv else nix) ];
          PYTHONDONTWRITEBYTECODE = "1";

          PYDD_WRITER = writer;
          PYDD_PROTOCOL = protocol;
          PYDD_SYSTEM = system;
          PYDD_MENU = builtins.toJSON menu;
        }
        # `tools/measure-modes.py` sets this, with `--impure`, so every planner
        # of a graph runs again and no node does. A pure evaluation reads "".
        // lib.optionalAttrs (builtins.getEnv "PYDD_MEASURE" != "") {
          PYDD_MEASURE = builtins.getEnv "PYDD_MEASURE";
        };

      # `allowImportFromDerivation` is an attribute of the planner, so
      # `overrideAttrs` reaches it, and `NIX_CONFIG` follows it here.
      realise =
        attrs:
        derivation (
          removeAttrs attrs [ "allowImportFromDerivation" ]
          // {
            # **Linux refuses an environment string over 128 KiB**, and the
            # menu of a real lock file is larger: `execve` fails with
            # "Argument list too long". So these go in as files.
            passAsFile = builtins.filter (name: attrs ? ${name}) [
              "PYDD_INPUTS"
              "PYDD_MENU"
            ];
            NIX_CONFIG = ''
              extra-experimental-features = nix-command ca-derivations dynamic-derivations
              allow-import-from-derivation = ${lib.boolToString attrs.allowImportFromDerivation}
            '';
          }
        );

      result =
        attrs:
        let
          planner = realise attrs;
        in
        {
          inherit planner;
          out = builtins.outputOf planner.outPath "out";
          /**
            Change the planner's attributes, and with them `out`.
          */
          overrideAttrs = f: result (attrs // f attrs);
          # What the planner runs on, as one derivation, for copying to a store.
          closure = linkFarmFromDrvs "${name}-planner-closure" [
            bash
            venv
            (if writer == "drv" then pyddDrv else nix)
          ];
        };
    in
    result attributes;
}
