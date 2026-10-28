# Real uv projects, pinned, built as they are on GitHub: live practice for
# what a lock file in the wild needs. Each entry says what, if anything, it
# needs beyond the project itself.
#
#     nix run --file . run -- live.cli.uv-fastapi-example
{ pkgs, pydd }:
let
  github =
    owner: repo: rev: hash:
    pkgs.fetchFromGitHub {
      inherit
        owner
        repo
        rev
        hash
        ;
    };
  projects = {
    uv-fastapi-example = {
      src =
        github "astral-sh" "uv-fastapi-example" "a1e313147b9be055167fe500d457d7c0b2f51318"
          "sha256-ewK4lEhTSRiT5CrXNukmKGOW3f5ENW4Y+aF4OZa83mY=";
      # Locked in 2024, for CPython 3.12. Newer: pydantic-core 2.20.1 has no
      # wheel for 3.14, and its sdist needs Rust and cargo's network;
      # httptools 0.6.1 ships C for the 3.12 API, which 3.13 changed.
      arguments.python = pkgs.python312;
      check = ''
        python -c 'import fastapi, httptools, pydantic, uvicorn, uvloop; print("fastapi", fastapi.__version__)'
        fastapi --help > /dev/null
      '';
    };
    # pdm-backend, editable; uvloop and httptools as binary wheels.
    fastapi-cli = {
      src =
        github "fastapi" "fastapi-cli" "093be9ca19f2ef44ba523aae0a98d6c9ea2e0888"
          "sha256-TzO2rtrJSjaNwCevNCZl4zS+Us09Q3Zd0bWJfNNia0g=";
      check = ''
        python -c 'import fastapi_cli, uvicorn; print("fastapi-cli", fastapi_cli.__version__)'
        fastapi --help > /dev/null
      '';
    };
    # pdm-backend; a dev group with mypy and pillow (manylinux libz).
    typer = {
      src =
        github "tiangolo" "typer" "b15210b71a5b9b35be82a1ffdb172ef7b3a89d85"
          "sha256-Rg5vFInHqLDObGTFQ8k86Yw45+lXGdLIcgVFV6Z/gB0=";
      check = ''
        python -c 'import typer, PIL.Image; print("typer", typer.__version__)'
        typer --help > /dev/null
      '';
    };
    # A workspace of hatchling and uv-dynamic-versioning, whose root is a
    # metapackage with no code.
    fastmcp = {
      src =
        github "jlowin" "fastmcp" "e2fcc412001ff7414095c5dc282f94c70299a612"
          "sha256-lhY94NoHClMQlP95lTT+Zg/TXZhYBy9Gv4EFQyv7Gkw=";
      check = ''
        python -c 'import fastmcp; print("fastmcp", fastmcp.__version__)'
        fastmcp --help > /dev/null
      '';
    };
    # A workspace of 17 members: hatchling, uv-dynamic-versioning, and a
    # setuptools member that writes an import finder.
    mcp-python-sdk = {
      src =
        github "modelcontextprotocol" "python-sdk" "754254c08fe1c3eda18b69da3d336106fe8d72d6"
          "sha256-3vncmONkKNoKoi1kCFYszfR+BP+rtelhY5Ua8ioBae4=";
      check = ''
        python -c 'import mcp, mcp.server.mcpserver; print("mcp", mcp.server.mcpserver.MCPServer.__name__)'
        # pyyaml 6.0.2 has no wheel for 3.14, and its sdist falls back to pure
        # Python when the link of libyaml fails.
        python -c 'import yaml; assert yaml.__with_libyaml__, "pyyaml without libyaml"'
      '';
    };
  };
in
builtins.mapAttrs (
  name: entry:
  let
    planner = pydd.python.uv (
      {
        project = entry.src;
        venvName = name;
      }
      // entry.arguments or { }
    );
    venv = planner.dynamic;
  in
  {
    # A smoke test inside the store that holds the environment: an import and
    # a console script. `../projects/check-uv.nix` gives the reason it runs as
    # a derivation.
    out = derivation {
      name = "${name}-check";
      inherit (pkgs.stdenv.hostPlatform) system;
      builder = "${pkgs.bash}/bin/bash";
      PATH = "${venv}/bin:${pkgs.coreutils}/bin";
      # A local project installs as an editable that reads this tree.
      PYDD_EDITABLE_ROOT = entry.src;
      args = [
        "-c"
        ''
          set -eu
          ${entry.check or "python -c 'import sys; print(sys.version)'"}
          echo ok > $out
        ''
      ];
    };
    environment = venv;
    root = planner;
    inherit (planner) closure;
  }
) projects
