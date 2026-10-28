{ pkgs, pydd }:
let
  inherit (pkgs) lib;

  npm =
    {
      # The directory that holds `package.json` and the lock: a package, or
      # the root of a workspace.
      project,
      # A directory that holds `project` and every file the lock names
      # outside it.
      root ? project,
      manifest ? lib.importJSON (project + "/package.json"),
      # The name of the root. A store path takes no `@` and no `/`, so a
      # scoped package `@scope/tool` gives `scope-tool`.
      name ? lib.replaceStrings [ "@" "/" ] [ "" "-" ] (
        manifest.name
          or (throw "pydd.node.npm: ${toString project}/package.json names no package. Pass `name`.")
      ),
      # The program that `meta.mainProgram` names, or `null`. By default the
      # program of the project's own name, or its only program.
      program ?
        let
          bins =
            if builtins.isString (manifest.bin or { }) then
              { ${name} = manifest.bin; }
            else
              manifest.bin or { };
        in
        if bins ? ${name} then
          name
        else if builtins.length (builtins.attrNames bins) == 1 then
          builtins.head (builtins.attrNames bins)
        else
          null,
      nodejs ? pkgs.nodejs,
      # npm's `--omit`: what the result leaves out. The build has every package.
      omit ? [ "dev" ],
      # The scripts of `package.json` that the root runs before it omits
      # anything, in the project and in each workspace member that has them.
      scripts ? [ "build" ],
      # What the install script of a package builds with, by package name, as
      # `{ canvas = [ pkgs.pkg-config pkgs.cairo.dev pkgs.cairo ]; }`. Each
      # entry reaches the script as `pydd.rust.cargo` gives a crate its tools:
      # `bin` on `PATH`, `lib/pkgconfig` on `PKG_CONFIG_PATH`, `lib` on
      # `LIBRARY_PATH`, `include` in `CFLAGS` and `CXXFLAGS`. Name each output
      # that the build reads, because a node runs no setup hook.
      buildTools ? { },
      # More of a package, by package name: `env`, as
      # `{ sharp.env.SHARP_FORCE_GLOBAL_LIBVIPS = "1"; }`, and `src`, a
      # tarball or a directory that replaces the package wherever the lock
      # installs it, as a patched or prebuilt copy.
      overrides ? { },
      # The hash of each git dependency, as `{ "isarray-2.0.5" = "sha256-..."; }`:
      # the archive of the commit, unpacked, as `fetchFromGitHub` takes it.
      # `nix store prefetch-file --unpack <url>` prints it, and the planner's
      # error names the URL.
      outputHashes ? { },
      # The lock that the planner reads: npm's, pnpm's, bun's or Yarn v1's. By
      # default the first of them that `project` holds.
      lockfile ? lib.findFirst (file: builtins.pathExists (project + "/${file}")) null [
        "package-lock.json"
        "pnpm-lock.yaml"
        "bun.lock"
        "yarn.lock"
      ],
      # More packages whose install scripts run, by name. pnpm and bun run
      # only the scripts that the project allows (`allowBuilds`,
      # `onlyBuiltDependencies`, `trustedDependencies`), and Yarn v1's lock
      # records no scripts.
      installScripts ? [ ],
      # The workspace members to install and build, by path, as
      # `[ "apps/server" ]`: each one, every member it links to, and the
      # project, as `pnpm --filter <member>...` takes them. `null`: all.
      members ? null,
      # The tool of the lock, on `PATH` for the root's scripts: a bun project's
      # `build` runs `bun run ...`. `null`: npm only.
      packageManager ?
        {
          "pnpm-lock.yaml" = pkgs.pnpm;
          "bun.lock" = pkgs.bun;
          "yarn.lock" = pkgs.yarn;
        }
        .${lockfile} or null,
      # The environment of the root's `scripts`, as
      # `{ VITE_API_URL = "/api"; }`. A package's install script takes
      # `overrides.<name>.env` instead.
      scriptEnv ? { },
    }:
    assert lib.assertMsg (lockfile != null) (
      "pydd.node.npm: ${toString project} holds no package-lock.json, pnpm-lock.yaml, bun.lock "
      + "or yarn.lock. The planner builds what the lock pins. Run `npm install --package-lock-only` "
      + "there, and commit the file."
    );
    assert lib.assertMsg (members == null || pydd.protocol != "out") (
      "pydd.node.npm: `members` filters the lock in the planner, and in out mode the host "
      + "makes every node. Use a mode with a planner socket."
    );
    let
      # `"pydd"` of `package.json`:
      #
      #     "pydd": { "buildTools": { "canvas": ["pkg-config", "cairo.dev", "cairo"] } }
      #
      # A value names an attribute of `pkgs`, and a dot walks into it.
      declared = manifest.pydd or { };
      attributeOf =
        package: attribute:
        let
          parts = lib.splitString "." attribute;
        in
        if lib.hasAttrByPath parts pkgs then
          lib.getAttrFromPath parts pkgs
        else
          throw (
            "pydd.node.npm: \"pydd\".buildTools of ${name} gives '${package}' "
            + "the name '${attribute}', and nixpkgs holds no attribute of that name."
          );
      tools =
        lib.mapAttrs (package: map (attributeOf package)) (declared.buildTools or { }) // buildTools;
      env = lib.zipAttrsWith (_: lib.foldl' (a: b: a // b) { }) [
        (lib.mapAttrs (_: override: override.env or { }) (declared.overrides or { }))
        (lib.mapAttrs (_: override: override.env or { }) overrides)
      ];
      sources = lib.mapAttrs (_: override: override.src) (
        lib.filterAttrs (_: override: override ? src) overrides
      );
      native = lib.mapAttrs (
        package: _:
        {
          tools = map toString (tools.${package} or [ ]);
          env = env.${package} or { };
        }
        // lib.optionalAttrs (sources ? ${package}) { src = "${sources.${package}}"; }
      ) (tools // env // sources);

      source =
        if builtins.isPath root then
          lib.cleanSourceWith {
            src = root;
            filter = path: _: baseNameOf path != "node_modules";
            name = "${name}-source";
          }
        else
          root;
      subdir =
        if root == project then
          "."
        else if builtins.isPath root then
          lib.path.removePrefix root project
        else
          lib.removePrefix "/" (lib.removePrefix (toString root) (toString project));
      # What an install script runs with: stdenv's own tools, a C compiler,
      # and the Python that node-gyp runs on.
      scriptTools = pkgs.stdenv.initialPath ++ [
        pkgs.stdenv.cc
        pkgs.python3
      ];
      # Each package that a node reads, with the derivation that builds it. A
      # node names that derivation as its input, as a string in Nix would.
      built = [
        nodejs
        pkgs.python3
        pkgs.cacert
      ]
      ++ scriptTools
      ++ lib.concatLists (builtins.attrValues tools)
      ++ builtins.filter lib.isDerivation (builtins.attrValues sources)
      ++ lib.optional (packageManager != null) packageManager;
      refs = builtins.listToAttrs (
        map (drv: {
          name = builtins.unsafeDiscardStringContext "${drv}";
          value = [
            (builtins.unsafeDiscardOutputDependency drv.drvPath)
            (drv.outputName or "out")
          ];
        }) built
      );
      menu = {
        inherit
          name
          subdir
          omit
          scripts
          native
          refs
          ;
        project = "${source}";
        nodejs = "${nodejs}";
        python = "${pkgs.python3}";
        script = "${../../src/pydd/node/scripts/package.py}";
        cacert = "${pkgs.cacert}";
        tools = map toString scriptTools;
        libc = if pkgs.stdenv.hostPlatform.isMusl then "musl" else "glibc";
        hashes = outputHashes;
        inherit lockfile;
        install_scripts = declared.installScripts or [ ] ++ installScripts;
      }
      # Only when set, so the menu of every other project keeps its hash.
      // lib.optionalAttrs (members != null) { inherit members; }
      // lib.optionalAttrs (packageManager != null) { manager = "${packageManager}"; }
      // lib.optionalAttrs (scriptEnv != { }) { script_env = scriptEnv; };
      # On released Nix the host makes each node, and the planner writes only
      # the root (`./graph.nix`). Each node's `.drv` is an input of the
      # planner, and nothing builds it until the root names it.
      hostNodes =
        if lockfile != "package-lock.json" then
          throw (
            "pydd.node.npm: out mode reads npm's lock on the host, and ${toString project} has "
            + "${lockfile}. Use a mode with a planner socket, or write package-lock.json."
          )
        else
          import ./graph.nix {
            inherit lib menu;
            inherit (pkgs.stdenv.hostPlatform) system;
            lock = lib.importJSON (project + "/package-lock.json");
          };
      planned = pydd.mkPlanner {
        inherit name menu;
        plan = pkgs.writeText "plan.py" "import sys\nfrom pydd.node._npm import main\nsys.exit(main())\n";
        modules = [
          ../../src/pydd/node/__init__.py
          ../../src/pydd/node/_npm.py
          ../../src/pydd/node/_readers.py
        ];
        pythonPath = lib.optional (lockfile == "pnpm-lock.yaml") pkgs.python3Packages.pyyaml;
        env = lib.optionalAttrs (pydd.protocol == "out") {
          PYDD_HOST_NODES = toString (
            map (node: builtins.unsafeDiscardOutputDependency node.drvPath) (builtins.attrValues hostNodes)
          );
        };
      };
    in
    import ../core/root.nix {
      inherit pkgs name program;
      application = planned.out;
      passthru =
        builtins.removeAttrs planned [
          "out"
          "overrideAttrs"
        ]
        // {
          dynamic = planned.out;
          # A development shell with node, npm and the tools of every package.
          shell = pkgs.mkShell {
            name = "${name}-shell";
            nativeBuildInputs = [ nodejs ];
            buildInputs = lib.unique (lib.concatLists (builtins.attrValues tools));
            env = lib.foldl' (a: b: a // b) { } (builtins.attrValues env);
          };
          # What the planner and the nodes run on, for copying to a store.
          closure = pkgs.linkFarmFromDrvs "${name}-closure" (
            [
              planned.closure
              nodejs
              pkgs.python3
              pkgs.cacert
            ]
            ++ scriptTools
          );
        };
    };
in
{
  /**
    An npm project, planned at build time from its `package-lock.json`: a
    fixed-output fetch and one node for each package.

    Takes `project`, the directory that holds `package.json` and
    `package-lock.json`. Returns an ordinary derivation that holds the
    project in `lib/node_modules/<name>` and its programs in `bin`, with
    `override` over these arguments. Its `passthru` holds `planner`,
    `dynamic` (the output that the planner submits), `shell` and `closure`.
  */
  npm = lib.makeOverridable npm;
}
