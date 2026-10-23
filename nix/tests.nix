# The suite, and the values only Nix can give it: each test compares what
# pydd writes with what Nix's own `derivation` makes of the same thing.
{
  lib,
  runCommand,
  nixVersions,
  ruff,
  huggorm,
  pydd-drv,
  nixvenv,
}:
let
  system = "x86_64-linux";

  # A floating leaf, and a node that reads it and one more store path.
  leafAttrs = {
    name = "leaf";
    inherit system;
    builder = "/bin/sh";
    args = [
      "-c"
      "echo leaf > $out"
    ];
    __contentAddressed = true;
    outputHashMode = "nar";
    outputHashAlgo = "sha256";
  };
  leaf = derivation leafAttrs;

  fixedAttrs = {
    name = "fixed";
    inherit system;
    builder = "/bin/sh";
    args = [
      "-c"
      "echo fixed > $out"
    ];
    outputHashMode = "flat";
    outputHashAlgo = "sha256";
    outputHash = builtins.hashString "sha256" "fixed\n";
  };
  fixed = derivation fixedAttrs;

  plain = s: builtins.unsafeDiscardStringContext s;

  # Without the leading slash: Nix rewrites this build's own placeholders
  # anywhere in its env, this one included.
  bare = output: builtins.substring 1 (-1) (builtins.placeholder output);

  # No key here is `outPath`: `builtins.toJSON` turns a set that has one into
  # that string.
  fixture = {
    placeholders = {
      out = bare "out";
      dev = bare "dev";
    };
    leaf = {
      attrs = leafAttrs;
      drvPath = plain leaf.drvPath;
      # A floating output's outPath is the placeholder another derivation
      # writes for it.
      downstream = plain leaf.outPath;
    };
    fixed = {
      attrs = fixedAttrs;
      drvPath = plain fixed.drvPath;
    };
  };
in
runCommand "pydd-tests"
  {
    nativeBuildInputs = [
      (nixvenv {
        python = huggorm.pythonModule;
        packages = ps: [
          ps.pytest
          ps.packaging
          ps.pyyaml
          huggorm
        ];
        executables = [ "pytest" ];
      })
      nixVersions.git
      ruff
    ];
    src = lib.fileset.toSource {
      root = ../.;
      fileset = lib.fileset.unions [
        ../pyproject.toml
        ../src
        ../tests
        ../examples
        ../lib/python/graph.nix
        ../lib/modes/nix-eval.nix
        ../lib/python/carry.nix
      ];
    };
    PYDD_FIXTURE = builtins.toJSON fixture;
    PYDD_DRV = lib.getExe pydd-drv;
  }
  ''
    cp -r $src source
    chmod -R u+w source
    cd source
    export HOME=$TMPDIR
    # `graph.nix` over every node kind, for `tests/test_render.py`. `outputOf`
    # reads the planner's `.drv`, so the oracle writes to a store of its own.
    NIX_CONFIG="extra-experimental-features = nix-command ca-derivations dynamic-derivations" \
      nix eval --store "local?root=$TMPDIR/oracle" --json --file tests/render-oracle.nix > $TMPDIR/oracle.json
    export PYDD_RENDER_ORACLE=$TMPDIR/oracle.json
    # `lib/modes/nix-eval.nix` gives the same `.drv` for every node kind, from
    # plain JSON, with `builtins.storePath` and `builtins.appendContext`.
    same=$(NIX_CONFIG="extra-experimental-features = nix-command ca-derivations dynamic-derivations" \
      nix eval --store "local?root=$TMPDIR/oracle" --file tests/nix-eval-oracle.nix \
      --apply 'r: builtins.all (v: v.same) (builtins.attrValues r)')
    if [ "$same" != true ]; then
      echo "lib/modes/nix-eval.nix and lib/python/graph.nix disagree on a node" >&2
      exit 1
    fi
    ruff check --no-cache .
    ruff format --no-cache --check .
    pytest -p no:cacheprovider tests
    touch $out
  ''
