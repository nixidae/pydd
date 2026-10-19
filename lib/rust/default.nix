{ pkgs, pydd }:
let
  inherit (pkgs) lib;

  cargo =
    {
      # The directory that holds `Cargo.toml` and `Cargo.lock`: a package, or
      # the root of a workspace.
      project,
      # A directory that holds `project` and every path dependency of it,
      # when one lies outside `project`.
      root ? project,
      # The name of the root. A virtual workspace has no package to take it
      # from.
      name ?
        (lib.importTOML (project + "/Cargo.toml")).package.name or (throw (
          "pydd.rust.cargo: ${toString project}/Cargo.toml is a virtual workspace, "
          + "so it names no package. Pass `name`."
        )),
      # The package set of the platform the binaries run on, such as
      # `pkgs.pkgsCross.aarch64-multiplatform`. The compiler and every build
      # script run on its `buildPackages`; the hints, the libraries and the
      # linker of the binaries come from it.
      targetPkgs ? pkgs,
      rustc ? targetPkgs.buildPackages.rustc,
      cargo ? targetPkgs.buildPackages.cargo,
      # The linker of the target's units.
      cc ? targetPkgs.stdenv.cc,
      # The linker of build scripts and proc-macros, which run where they build.
      hostCc ? targetPkgs.buildPackages.stdenv.cc,
      target ? targetPkgs.stdenv.hostPlatform.rust.rustcTarget,
      # The program that `meta.mainProgram` names, or `null`.
      program ? if (lib.importTOML (project + "/Cargo.toml")) ? package then name else null,
      # What cargo builds, as its own flags say: `--package` for each of
      # `packages` (none: the default members), `--features`,
      # `--no-default-features`, `--all-features` and `--profile`.
      packages ? [ ],
      features ? [ ],
      noDefaultFeatures ? false,
      allFeatures ? false,
      profile ? "release",
      # For a git dependency, as `rustPlatform.importCargoLock` takes them:
      # the hash of each git crate, as `{ "itoa-1.0.15" = "sha256-..."; }`,
      # or `allowBuiltinFetchGit`, which fetches at evaluation.
      outputHashes ? { },
      allowBuiltinFetchGit ? false,
      # Take the tools and environment of each crate from nixpkgs'
      # `defaultCrateOverrides` (`./hints.nix`).
      nixpkgsHints ? true,
      # What the nodes of a crate build with, by crate name, as
      # `{ openssl-sys = [ pkgs.pkg-config pkgs.openssl.dev pkgs.openssl ]; }`.
      # It replaces the hints of that crate. Each entry reaches the build
      # script and the compile of the crate: `bin` on `PATH`,
      # `lib/pkgconfig` and `share/pkgconfig` on `PKG_CONFIG_PATH`, `lib` on
      # `LIBRARY_PATH`, and `include` as `-isystem` in `CFLAGS` and
      # `CXXFLAGS`. A node runs no
      # setup hook, so name each output that the build reads.
      buildTools ? { },
      # More of a crate, by crate name, as
      # `{ zstd-sys.env.ZSTD_SYS_USE_PKG_CONFIG = "1"; }`. `env` adds to the
      # hints of the crate, and wins per variable.
      overrides ? { },
      # Store paths placed into the source tree, by path relative to `root`,
      # as `{ dist = "${frontend}/dist"; }` for a crate that embeds a web
      # build. The lock and the manifests are still read from `project`, so
      # evaluation does not wait on these builds.
      files ? { },
    }:
    assert lib.assertMsg (builtins.pathExists (project + "/Cargo.lock")) (
      "pydd.rust.cargo: ${toString project} holds no Cargo.lock. The planner builds what "
      + "the lock pins, offline. Run `cargo generate-lockfile` there, and commit the file."
    );
    assert lib.assertMsg (pydd.protocol != "out") (
      "pydd.rust.cargo: plannerProtocol = \"out\" needs the host to make every node, "
      + "and only cargo, in a build, knows the units. Use \"rpc\" or \"recursive\"."
    );
    let
      # `[package.metadata.pydd]` of the project, or `[workspace.metadata.pydd]`.
      #
      #     [package.metadata.pydd.build-tools]
      #     openssl-sys = ["pkg-config", "openssl.dev", "openssl"]
      #
      #     [package.metadata.pydd.overrides.zstd-sys]
      #     env = { ZSTD_SYS_USE_PKG_CONFIG = "1" }
      #
      # A value of `build-tools` names an attribute of `pkgs`, and a dot
      # walks into it.
      manifest = lib.importTOML (project + "/Cargo.toml");
      declared = (manifest.workspace.metadata.pydd or { }) // (manifest.package.metadata.pydd or { });
      attributeOf =
        crate: attribute:
        let
          parts = lib.splitString "." attribute;
        in
        if lib.hasAttrByPath parts targetPkgs then
          lib.getAttrFromPath parts targetPkgs
        else
          throw (
            "pydd.rust.cargo: [package.metadata.pydd.build-tools] of ${name} gives '${crate}' "
            + "the name '${attribute}', and nixpkgs holds no attribute of that name."
          );
      lock = lib.importTOML (project + "/Cargo.lock");
      hinted = lib.optionalAttrs nixpkgsHints (
        import ./hints.nix {
          inherit lib;
          pkgs = targetPkgs;
        } lock.package
      );
      tools =
        lib.mapAttrs (_: hint: hint.tools) hinted
        // lib.mapAttrs (crate: map (attributeOf crate)) (declared.build-tools or { })
        // buildTools;
      env = lib.zipAttrsWith (_: lib.foldl' (a: b: a // b) { }) [
        (lib.mapAttrs (_: hint: hint.env) hinted)
        (lib.mapAttrs (_: override: override.env or { }) (declared.overrides or { }))
        (lib.mapAttrs (_: override: override.env or { }) overrides)
      ];
      native = lib.mapAttrs (crate: _: {
        tools = map toString (tools.${crate} or [ ]);
        env = env.${crate} or { };
      }) (tools // env);

      # A store path, such as a fetch, is taken as it is.
      tree =
        if builtins.isPath root then
          lib.cleanSourceWith {
            src = root;
            filter = path: _: baseNameOf path != "target";
            name = "${name}-source";
          }
        else
          root;
      source =
        if files == { } then
          tree
        else
          pkgs.runCommand "${name}-source" { } (
            ''
              cp -r ${tree} $out
              chmod -R u+w $out
            ''
            + lib.concatStrings (
              lib.mapAttrsToList (path: file: ''
                mkdir -p "$out/${dirOf path}"
                rm -rf "$out/${path}"
                cp -r ${file} "$out/${path}"
              '') files
            )
          );
      subdir =
        if root == project then
          "."
        else if builtins.isPath root then
          lib.path.removePrefix root project
        else
          lib.removePrefix "/" (lib.removePrefix (toString root) (toString project));
      # nixpkgs makes one fetch for each crate of the lock, and this
      # directory links to each one. A node then reads only its own crate.
      vendor = pkgs.rustPlatform.importCargoLock {
        lockFile = project + "/Cargo.lock";
        inherit outputHashes allowBuiltinFetchGit;
      };
      arguments = [
        "--profile"
        profile
      ]
      ++ lib.concatMap (package: [
        "--package"
        package
      ]) packages
      ++ lib.optionals (features != [ ]) [
        "--features"
        (lib.concatStringsSep "," features)
      ]
      ++ lib.optional noDefaultFeatures "--no-default-features"
      ++ lib.optional allFeatures "--all-features";
      # `build` plans the binaries, and `test` a graph that runs the tests.
      plannerFor =
        command:
        pydd.mkPlanner {
          name = if command == "test" then "${name}-tests" else name;
          plan = pkgs.writeText "plan.py" "import sys\nfrom pydd.rust._cargo import main\nsys.exit(main())\n";
          modules = [
            ../../src/pydd/rust/__init__.py
            ../../src/pydd/rust/_cargo.py
          ];
          menu = {
            inherit
              name
              target
              subdir
              arguments
              ;
            project = "${source}";
            vendor = "${vendor}";
            cargo = "${cargo}";
            rustc = "${rustc}";
            cc = "${cc}";
            host = targetPkgs.stdenv.buildPlatform.rust.rustcTarget;
            host_cc = "${hostCc}";
            prefix = cc.targetPrefix;
            bintools = "${cc.bintools}";
            coreutils = "${pkgs.coreutils}";
            python = "${pkgs.python3}";
            script = "${../../src/pydd/rust/scripts/unit.py}";
            inherit native;
          }
          // lib.optionalAttrs (command == "test") { inherit command; };
        };
      planned = plannerFor "build";
      tested = plannerFor "test";
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
          # Every test of the project, from `cargo test`: a node compiles each
          # test binary, and one more runs it. A crate's doctests are one node.
          # It builds when every test passes, and holds their logs.
          # A development shell for the project: cargo, rustc, clippy,
          # rustfmt and rust-analyzer, and the tools and variables of every
          # crate, through stdenv's setup hooks as a nixpkgs build gets them.
          shell = pkgs.mkShell {
            name = "${name}-shell";
            nativeBuildInputs = [
              cargo
              rustc
              pkgs.clippy
              pkgs.rustfmt
              pkgs.rust-analyzer
            ];
            buildInputs = lib.unique (lib.concatLists (builtins.attrValues tools));
            env = lib.foldl' (a: b: a // b) { } (builtins.attrValues env);
          };
          tests = import ../core/root.nix {
            inherit pkgs;
            name = "${name}-tests";
            application = tested.out;
            passthru.dynamic = tested.out;
          };
          # What the planner and the nodes run on, for copying to a store.
          closure = pkgs.linkFarmFromDrvs "${name}-closure" [
            planned.closure
            vendor
            cargo
            rustc
            cc
            hostCc
            cc.bintools
            pkgs.coreutils
            pkgs.python3
          ];
        };
    };
in
{
  /**
    A Cargo project, planned at build time: one node for each unit of
    cargo's unit graph.

    Takes `project`, the directory that holds `Cargo.toml` and `Cargo.lock`.
    Returns an ordinary derivation of the project's binaries, with
    `override` over these arguments. Its `passthru` holds `planner`,
    `dynamic` (the output that the planner submits), `tests` (a derivation
    that runs every test of the project) and `closure`.
  */
  cargo = lib.makeOverridable cargo;
}
