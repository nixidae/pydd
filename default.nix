{
  sources ? import ./nix/sources.nix,
  huggormSet ? import sources.huggorm { inherit sources; },
  # huggorm's package set, so the bindings and the planners share one Python.
  pkgs ? huggormSet.pkgs,
}:
let
  inherit (pkgs) lib;

  # builder-rpc-v0 is on Nix master and in no release.
  nix = pkgs.nixVersions.git;

  # The huggorm lane whose Nix has builder-rpc-v0.
  huggorm = huggormSet.lanes.git.huggorm;

  pydd = pkgs.callPackage ./lib { inherit huggorm nix; };

  # The newest release, for planners that write `$out`. Its huggorm lane
  # renders the ATerm that this Nix parses.
  nixReleased = pkgs.nixVersions.nix_2_35;
  huggormReleased = huggormSet.lanes.nix_2_35.huggorm;

  # Writes derivations from a build, as JSON, for a planner in any language
  # (nixidae/nixidae#782). One per Nix: only master has `builder-rpc-v0`.
  pydd-drv = pkgs.callPackage ./tools/pydd-drv { inherit (nix.libs) nix-store; };
  pydd-drv-released = pkgs.callPackage ./tools/pydd-drv { inherit (nixReleased.libs) nix-store; };

  # The evaluator in the planner is huggorm's, and so is the staging store of
  # a planner that writes `$out`.
  writersOf =
    name:
    if
      lib.elem name [
        "evaluated"
        "nested"
        "released"
      ]
    then
      [ "huggorm" ]
    else if name == "graph" then
      [
        "drv"
        "released"
        "recursive"
      ]
    else
      [
        "cli"
        "huggorm"
        "drv"
      ];
in
rec {
  inherit
    pkgs
    pydd
    huggorm
    pydd-drv
    pydd-drv-released
    ;

  # nix run --file . run -- examples.hello.cli
  # nix run --file . run -- examples.venv.huggorm check
  examples = lib.mapAttrs (
    name: _:
    lib.genAttrs (writersOf name) (
      writer:
      pkgs.callPackage ./examples/${name} {
        pydd = pkgs.callPackage ./lib (
          if name == "released" then
            {
              inherit writer;
              huggorm = huggormReleased;
              nix = nixReleased;
            }
          else if writer == "released" || writer == "recursive" then
            {
              writer = "drv";
              huggorm = huggormReleased;
              nix = nixReleased;
              pyddDrv = pydd-drv-released;
              plannerProtocol = if writer == "recursive" then "recursive" else "out";
            }
          else
            {
              inherit huggorm nix writer;
              pyddDrv = pydd-drv;
            }
        );
      }
    )
  ) (lib.filterAttrs (_: type: type == "directory") (builtins.readDir ./examples));

  # Build in a private store with a given Nix, because the daemon of this
  # machine may be neither Nix master nor a release: `tools/runner.py`. The
  # store keeps /nix/store as its store directory, so the cache substitutes
  # into it.
  mkRunner =
    name: arguments:
    pkgs.writeShellApplication {
      inherit name;
      runtimeInputs = [ nix ];
      text = ''
        export PYDD_NIX_MASTER=${lib.getExe nix}
        export PYDD_NIX_RELEASED=${lib.getExe nixReleased}
        export PYDD_NIX_RELEASED_VERSION=${nixReleased.version}
        exec ${
          pkgs.python3.withPackages (ps: [ ps.anyio ])
        }/bin/python3 ${./tools/runner.py} ${lib.escapeShellArgs arguments} "$@"
      '';
    };

  # Nix master, with `builder-rpc-v0`.
  # nix run --file . run -- examples.hello.cli
  run = mkRunner "pydd-run" [
    "run"
    "--lane"
    "master"
  ];

  # A stock release, which has no `builder-rpc-v0`.
  # nix run --file . run-released -- examples.released.huggorm
  run-released = mkRunner "pydd-run-released" [
    "run"
    "--lane"
    "released"
  ];

  # The planner of each project's environment. With PYDD_MEASURE set and
  # `--impure`, every planner carries it (`mkPlanner`), so Nix runs each one
  # again and every node stays cached. `--rebuild` refuses a
  # content-addressed planner. `tools/measure-modes.py` sets it.
  measure = lib.mapAttrs (_: lib.mapAttrs (_: project: project.out.planner.planner)) projects;

  # The workflow as a value, from ci/workflows/pages.nix, and as the file GitHub
  # reads. `ghalib.toYamlScript` writes a multi-line `run:` as a literal
  # block and quotes `'on'`; pynixd's default.nix gives the reasons.
  ghalib = import sources.ghanix { inherit lib; };
  ciWorkflow = import ./ci/workflows/pages.nix {
    inherit lib ghalib;
    nixVersion = pkgs.nix.version;
  };
  ciWorkflowFile =
    pkgs.runCommand "pages.yml"
      {
        nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pyyaml ])) ];
        value = builtins.toJSON ciWorkflow;
        passAsFile = [ "value" ];
      }
      ''
        {
          echo "# GENERATED FILE -- do not edit by hand."
          echo "# Edit ci/workflows/pages.nix, then run: nix run --file . ci-workflow-update"
          python3 ${ghalib.toYamlScript} "$valuePath"
        } > $out
      '';
  ci-workflow-update = pkgs.writeShellScriptBin "ci-workflow-update" ''
    cp --no-preserve=mode ${ciWorkflowFile} .github/workflows/pages.yml
  '';

  # The site that GitHub Pages serves: docs/, with Sphinx.
  pydd-docs = pkgs.python3Packages.callPackage ./nix/docs.nix { inherit (pkgs) graphviz; };

  # A derivation that does nothing, with the same nonce: the time Nix needs to
  # build anything at all, which every planner time holds.
  measure-floor = derivation {
    name = "floor";
    inherit (pkgs.stdenv.hostPlatform) system;
    builder = "${pkgs.bash}/bin/bash";
    args = [
      "-c"
      "echo > $out"
    ];
    PYDD_MEASURE = builtins.getEnv "PYDD_MEASURE";
  };

  # Writes docs/_generated, which docs/modes.md reads. Run it after
  # `test-projects`, so only the planners run.
  measure-modes = pkgs.writeShellApplication {
    name = "pydd-measure-modes";
    runtimeInputs = [
      nix
      pkgs.python3
      pkgs.jujutsu
    ];
    text = ''
      export NIX_MASTER=${lib.getExe nix}
      export NIX_RELEASED=${lib.getExe nixReleased}
      export NIX_RELEASED_VERSION=${nixReleased.version}
      exec python3 ${./tools/measure-modes.py} "$@"
    '';
  };

  # A stock release, with recursive-nix: a planner gets a daemon socket.
  # nix run --file . run-recursive -- examples.graph.recursive
  run-recursive = mkRunner "pydd-run-recursive" [
    "run"
    "--lane"
    "recursive"
  ];

  checks = {
    # nixpkgs in the planner and stdenv as data give one `.drv` for every
    # source node of the render oracle. The comparison runs at evaluation.
    stdenvAsData =
      let
        compared = import ./tests/stdenv-oracle.nix { nixpkgs = pkgs.path; };
        differ = lib.filterAttrs (_: node: !node.same) compared;
      in
      assert lib.assertMsg (
        differ == { }
      ) "stdenv as data differs from mkDerivation: ${builtins.toJSON differ}";
      pkgs.runCommand "stdenv-as-data" { } "echo ${toString (builtins.attrNames compared)} > $out";

    # nixpkgs gives pyyaml `libyaml.dev`, and the hints must still hold the
    # library itself.
    hints =
      let
        hints = import ./lib/python/hints.nix {
          inherit lib pkgs;
          python = pkgs.python3;
        } [ "pyyaml" ];
        names = map (drv: drv.outPath) hints.pyyaml;
      in
      assert lib.assertMsg (builtins.elem pkgs.libyaml.out.outPath names)
        "hints of pyyaml lack ${pkgs.libyaml.out}: ${toString names}";
      pkgs.runCommand "hints" { } "echo ${toString names} > $out";

    # Does .github/workflows/pages.yml still say what ci/workflows/pages.nix says?
    # Parsed, so key order and quoting cannot fail it.
    ciWorkflow =
      pkgs.runCommand "ci-workflow-check"
        {
          nativeBuildInputs = [
            pkgs.yq-go
            pkgs.jq
          ];
        }
        ''
          yq --output-format=json '.' ${./.github/workflows/pages.yml} | jq --sort-keys . > committed.json
          jq --sort-keys . < ${pkgs.writeText "ci-rendered.json" (builtins.toJSON ciWorkflow)} > rendered.json
          if ! diff --unified committed.json rendered.json; then
            echo ".github/workflows/pages.yml and ci/workflows/pages.nix disagree." >&2
            echo "Run: nix run --file . ci-workflow-update" >&2
            exit 1
          fi
          touch $out
        '';
    tests = pkgs.callPackage ./nix/tests.nix { inherit huggorm pydd-drv; };
  };

  # The uv projects of the ddrn lab, each with its check, once for each
  # mode. `cli` and `huggorm` are `builder-rpc-v0` on Nix master, and the
  # writer chooses the path from plan to derivations. `out` is stock Nix
  # 2.35, where the host makes every node that a planner names but its root.
  # nix run --file . run -- projects.cli.pure-app
  # nix run --file . run-released -- projects.out.pure-app
  projects =
    lib.genAttrs [ "cli" "huggorm" "drv" ] (
      writer:
      import ./tests/projects {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          inherit huggorm nix writer;
          pyddDrv = pydd-drv;
        };
      }
    )
    // {
      out = (
        import ./tests/projects {
          inherit pkgs;
          pydd = pkgs.callPackage ./lib {
            huggorm = huggormReleased;
            nix = nixReleased;
            writer = "huggorm";
            plannerProtocol = "out";
          };
        }
      );
      # recursive-nix with EnsurePath used: a stock `nix eval` evaluates
      # graph.nix in the planner, with `builtins.storePath` and
      # `builtins.appendContext`. No huggorm, and no renderer.
      recursive-eval = (
        import ./tests/projects {
          inherit pkgs;
          pydd = pkgs.callPackage ./lib {
            huggorm = huggormReleased;
            nix = nixReleased;
            writer = "cli";
            plannerProtocol = "recursive";
            plannerRender = "nix-cli";
          };
        }
      );
      # recursive-eval, with nixpkgs in the planner: a source build is a
      # `stdenv.mkDerivation`. Its environments differ from every other mode's.
      recursive-nixpkgs = (
        import ./tests/projects {
          inherit pkgs;
          pydd = pkgs.callPackage ./lib {
            huggorm = huggormReleased;
            nix = nixReleased;
            writer = "cli";
            plannerProtocol = "recursive";
            plannerRender = "nix-cli";
            plannerNixpkgs = pkgs.path;
          };
        }
      );
      # stdenv as data on builder-rpc-v0: the host evaluates
      # `stdenv.mkDerivation` once, and a planner with no nixpkgs writes the
      # node it would write. Through the Python renderer, with `nix`.
      stdenv = (
        import ./tests/projects {
          inherit pkgs;
          pydd = pkgs.callPackage ./lib {
            inherit huggorm nix;
            writer = "cli";
            plannerStdenv = pkgs;
          };
        }
      );
      # stdenv as data on released Nix, with no socket at all.
      out-stdenv = (
        import ./tests/projects {
          inherit pkgs;
          pydd = pkgs.callPackage ./lib {
            huggorm = huggormReleased;
            nix = nixReleased;
            writer = "huggorm";
            plannerProtocol = "out";
            plannerStdenv = pkgs;
          };
        }
      );
      # Stock 2.35 with recursive-nix: the planner writes through the build's
      # daemon socket, with the release's own `nix`, and its root to `$out`.
      recursive = (
        import ./tests/projects {
          inherit pkgs;
          pydd = pkgs.callPackage ./lib {
            huggorm = huggormReleased;
            nix = nixReleased;
            writer = "cli";
            plannerProtocol = "recursive";
          };
        }
      );
    };

  # Real uv projects from GitHub, as they are: `tests/live`.
  live =
    lib.genAttrs [ "cli" "huggorm" ] (
      writer:
      import ./tests/live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib { inherit huggorm nix writer; };
      }
    )
    // {
      out = import ./tests/live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "huggorm";
          plannerProtocol = "out";
        };
      };
      recursive = import ./tests/live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "cli";
          plannerProtocol = "recursive";
        };
      };
    };

  # Cargo projects of `tests/rust`, in each mode where a planner writes its
  # own nodes. `out` is not one: there the host makes every node, and only
  # cargo, in a build, knows the units.
  # nix run --file . run -- rust.cli.serde-app
  rust =
    lib.genAttrs [ "cli" "huggorm" "drv" ] (
      writer:
      import ./tests/rust {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          inherit huggorm nix writer;
          pyddDrv = pydd-drv;
        };
      }
    )
    // {
      recursive = import ./tests/rust {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "cli";
          plannerProtocol = "recursive";
        };
      };
    };

  # npm projects of `tests/node`, in each mode.
  # nix run --file . run -- node.cli.hello-npm
  node =
    lib.genAttrs [ "cli" "huggorm" "drv" ] (
      writer:
      import ./tests/node {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          inherit huggorm nix writer;
          pyddDrv = pydd-drv;
        };
      }
    )
    // {
      recursive = import ./tests/node {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "cli";
          plannerProtocol = "recursive";
        };
      };
      # Released Nix with no socket: the host makes every node
      # (`lib/node/graph.nix`), and the planner writes the root.
      out = import ./tests/node {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "huggorm";
          plannerProtocol = "out";
        };
      };
    };

  # Real npm projects, at the releases that nixpkgs builds: `tests/node-live`.
  node-live =
    lib.genAttrs [ "cli" "huggorm" ] (
      writer:
      import ./tests/node-live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib { inherit huggorm nix writer; };
      }
    )
    // {
      recursive = import ./tests/node-live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "cli";
          plannerProtocol = "recursive";
        };
      };
      # Released Nix with no socket: the host makes every node
      # (`lib/node/graph.nix`), and the planner writes the root.
      out = import ./tests/node-live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "huggorm";
          plannerProtocol = "out";
        };
      };
    };

  # Two revisions of a real npm project: `tests/node-revisions`.
  node-revisions = import ./tests/node-revisions {
    inherit pkgs;
    pydd = pkgs.callPackage ./lib {
      inherit huggorm nix;
      writer = "cli";
    };
  };

  # Real Cargo projects from GitHub, as they are: `tests/rust-live`.
  rust-live =
    lib.genAttrs [ "cli" "huggorm" ] (
      writer:
      import ./tests/rust-live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib { inherit huggorm nix writer; };
      }
    )
    // {
      recursive = import ./tests/rust-live {
        inherit pkgs;
        pydd = pkgs.callPackage ./lib {
          huggorm = huggormReleased;
          nix = nixReleased;
          writer = "cli";
          plannerProtocol = "recursive";
        };
      };
    };

  # Run every check of a test set in each mode, and compare the environments
  # and the roots. A check needs a Nix that this machine's daemon may not be,
  # so it builds in the private store of a lane, and no `checks` attribute
  # can hold it.
  #
  #     nix run --file . test-projects -- [--jobs N] [name ...]
  test-projects = mkRunner "pydd-test-projects" [
    "test"
    "projects"
  ];

  # The live projects of `tests/live`, in one mode of each level of
  # restriction: `builder-rpc-v0` through `nix` and through huggorm,
  # recursive-nix, and a release with no socket.
  test-live = mkRunner "pydd-test-live" [
    "test"
    "live"
  ];

  # The real npm projects of `tests/node-live`.
  test-node-live = mkRunner "pydd-test-node-live" [
    "test"
    "node-live"
  ];

  # The real Cargo projects of `tests/rust-live`.
  test-rust-live = mkRunner "pydd-test-rust-live" [
    "test"
    "rust-live"
  ];

  # The Cargo projects of `tests/rust`.
  test-rust = mkRunner "pydd-test-rust" [
    "test"
    "rust"
  ];

  # The npm projects of `tests/node`.
  test-node = mkRunner "pydd-test-node" [
    "test"
    "node"
  ];
}
