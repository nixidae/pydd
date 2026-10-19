# What nixpkgs knows about building a crate, as tools and environment for
# the nodes of that crate (nixidae/nixidae#798).
#
# `defaultCrateOverrides` gives `buildRustCrate` the native inputs of a crate
# that links a system library, such as `openssl-sys`. The hints read the same
# table, so an overlay that changes it changes what pydd builds with.
{ lib, pkgs }:
let
  inherit (import ../core/tools.nix { inherit lib; }) isHook outputsOf withPropagated;

  # The table also holds attributes of `buildRustCrate` itself, such as
  # `extraLinkFlags`. An environment variable is in capitals, as
  # `ZSTD_SYS_USE_PKG_CONFIG` of zstd-sys is.
  isVariable = name: builtins.match "[A-Z][A-Z0-9_]*" name != null;
  setting = value: if builtins.isBool value then (if value then "1" else "") else toString value;
  envOf =
    attrs:
    lib.mapAttrs (_: setting) (
      lib.filterAttrs (
        name: value:
        isVariable name && (builtins.isString value || builtins.isBool value || builtins.isInt value)
      ) attrs
    );

  hintOf =
    crate:
    let
      attrs = pkgs.defaultCrateOverrides.${crate.name} {
        crateName = crate.name;
        inherit (crate) version;
      };
      # **A native input runs where the build runs.** In a cross package
      # set, `pkg-config` is spliced, and its plain value is built for the
      # target: an aarch64 pkg-config that the sandbox cannot run.
      # `__spliced.buildHost` is the one for this machine; a native set
      # splices nothing.
      buildSide = drv: drv.__spliced.buildHost or drv;
      inputs = builtins.filter (drv: lib.isDerivation drv && !(isHook drv)) (
        map buildSide (attrs.nativeBuildInputs or [ ])
        ++ attrs.buildInputs or [ ]
        ++ attrs.propagatedBuildInputs or [ ]
      );
    in
    {
      tools = lib.unique (lib.concatMap outputsOf (withPropagated inputs));
      env = envOf attrs;
    };

  # An entry may read an attribute of the crate that this call does not
  # give, and then it throws.
  tried =
    crate:
    let
      result = builtins.tryEval (
        let
          hint = hintOf crate;
        in
        builtins.deepSeq (map lib.getName hint.tools) (builtins.deepSeq hint.env hint)
      );
    in
    if pkgs.defaultCrateOverrides ? ${crate.name} && result.success then result.value else null;
in
/**
  The hints of nixpkgs for each crate of `crates`, a list of
  `{ name, version }`, by crate name. A crate that the table does not hold,
  or whose entry names no input and no variable, gets no entry.
*/
crates:
lib.filterAttrs (_: hint: hint != null && (hint.tools != [ ] || hint.env != { })) (
  builtins.listToAttrs (
    map (crate: {
      inherit (crate) name;
      value = tried crate;
    }) crates
  )
)
