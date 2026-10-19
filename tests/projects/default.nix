# Every check of the test projects, by name, in the shape that `run` builds:
# `out` is the check, `environment` is the environment it checks, `root` is
# the derivation around it, and `closure` is what the planners run on.
#
#     nix run --file . run -- projects.cli.pure-app
#     nix run --file . test-projects
#
# The variants are the ones the ddrn lab's `test_projects.py` ran: one lock
# file, and the selections and forms that it does not record.
{ pkgs, pydd }:
let
  check = file: arguments: import file ({ inherit pkgs pydd; } // arguments);
  everyMember = [
    "ws-cli"
    "ws-core"
    "rich"
    "markdown-it-py"
    "mdurl"
    "pygments"
  ];
  checks = {
    pure-app = check ./check-uv.nix { };
    packaged-app = check ./check-packaged.nix { };
    packaged-sdist = check ./check-sdist.nix { };
    workspace-all = check ./check-workspace.nix {
      venvName = "workspace-all";
      expect = everyMember;
    };
    workspace-core = check ./check-workspace.nix {
      venvName = "workspace-core";
      select = {
        ws-core = [ ];
      };
      expect = [ "ws-core" ];
    };
    workspace-core-sdist = check ./check-workspace.nix {
      venvName = "workspace-core-sdist";
      expect = everyMember;
      fromSdist = [ "ws-core" ];
    };
    application = check ./check-application.nix { };
    packaged-app-bin = check ./check-application.nix {
      project = ./packaged-app;
      editableTree = ./packaged-app;
      application = [ "packaged-app" ];
      venvName = "packaged-app-bin";
    };
    binary-app = check ./check-binary.nix { };
    binary-nixpkgs = check ./check-binary.nix {
      venvName = "binary-nixpkgs";
      fromNixpkgs.pyzmq = pkgs.python3Packages.pyzmq;
    };
    native-app = check ./check-native.nix { };
    zmq-sdist = check ./check-binary.nix {
      project = ./zmq-sdist;
      venvName = "zmq-sdist";
      linksTo = "${pkgs.zeromq}";
    };
    # No hints, and no libsodium: the fixup finds it through the RUNPATH of
    # libzmq (nixidae/nixidae#785).
    zmq-sdist-transitive = check ./check-binary.nix {
      project = ./zmq-sdist;
      venvName = "zmq-sdist-transitive";
      linksTo = "${pkgs.zeromq}";
      nixpkgsHints = false;
      buildTools.pyzmq = [
        pkgs.stdenv.cc
        pkgs.cmake
        pkgs.ninja
        pkgs.zeromq
      ];
    };
    # BLAS through PEP 517 config_settings, from a node override
    # (nixidae/nixidae#786). nixpkgs hints give the rest.
    numpy-sdist = check ./check-native.nix {
      project = ./numpy-sdist;
      venvName = "numpy-sdist";
      script = ./scripts/check-numpy.py;
    };
    # psutil from a stdenv build that the host wrote, the escape hatch
    # (nixidae/nixidae#787). The planner still selects the sdist, and cffi is
    # still a planned node.
    native-host = check ./check-native.nix {
      project = ./native-app;
      venvName = "native-host";
      script = ./scripts/check-host.py;
      builders.psutil =
        artifact:
        if artifact.kind != "sdist" then
          null
        else
          pkgs.python3.pkgs.buildPythonPackage {
            pname = artifact.package;
            inherit (artifact) version src;
            pyproject = true;
            build-system = [ pkgs.python3.pkgs.setuptools ];
            postInstall = "touch $out/${pkgs.python3.sitePackages}/pydd_host_built";
          };
    };
    root-derivation = check ./check-root.nix { };
    git-app = check ./check-git.nix { };
    vendored-app = check ./check-vendored.nix { };
    local-index = check ./check-index.nix { };
    wheel-features = check ./check-wheel-features.nix { };
    abi-gil = check ./check-probe.nix {
      project = ./abi-probe;
      probe = "abiprobe.ABI";
      expected = "gil";
    };
    abi-free-threaded = check ./check-probe.nix {
      project = ./abi-probe;
      probe = "abiprobe.ABI";
      python = pkgs.python313FreeThreading;
      expected = "free-threaded";
    };
    abi-pypy = check ./check-probe.nix {
      project = ./abi-probe;
      probe = "abiprobe.ABI";
      python = pkgs.pypy3;
      expected = "pypy";
    };
    # glibc 2.44 at the time of writing. The check names the version it
    # expects, so a glibc below 2.40 fails it on purpose.
    platform-glibc = check ./check-probe.nix {
      project = ./platform-probe;
      probe = "platformprobe.PLATFORM";
      expected = "manylinux_2_40";
    };
    # The backend asks for `hookprobe-extra` only through
    # `get_requires_for_build_wheel` (nixidae/nixidae#847).
    hook-requires = check ./check-probe.nix {
      project = ./hook-probe;
      probe = "hookprobe_app.HOOK";
      expected = "extra";
    };
  };
  # Every planner here runs on the same interpreter and the same `nix`.
  inherit (pydd.python.uv { project = ./pure-app; }) closure;
in
builtins.mapAttrs (_: out: {
  inherit out closure;
  environment = out.planner.dynamic;
  root = out.planner;
}) checks
