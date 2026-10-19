# Per-package node overrides, as data (nixidae/nixidae#786).
#
# A package that needs more than its lock file says gets it here, and the
# planner writes it into the node that builds that package. So an override
# works in every mode, released Nix included. `graph.nix` gives the meaning of
# each key, at `overridesOf`.
#
# A project declares them in `pyproject.toml`, and a caller passes them to
# `uv.nix` or `poetry.nix` as `overrides`. The argument wins, per key:
#
#     [tool.pydd.overrides.numpy]
#     config-settings = { setup-args = ["-Dblas=openblas", "-Dlapack=openblas"] }
#     patches = ["patches/numpy-no-sse.patch"]
#     env = { NPY_DISABLE_SVML = "1" }
#     pre-build = "rm -rf vendored-meson/meson/test cases"
#     post-install = "rm -r $out/bin"
#     fixup = false
#     build-tools = ["openblas"]
#
#     overrides.numpy = { configSettings = { ... }; patches = [ ./x.patch ]; };
#
# A patch in `pyproject.toml` is a path relative to the project. A build tool
# there names an attribute of `pkgs`, and a dot walks into it.
{
  lib,
  pkgs,
  project,
  projectName,
  # `[tool.pydd.overrides]` of the project.
  declared,
  # The `overrides` argument of the reader.
  overrides,
}:
let
  # The spelling in `pyproject.toml`, and the spelling in Nix.
  keys = {
    env = "env";
    config-settings = "configSettings";
    patches = "patches";
    pre-build = "preBuild";
    post-install = "postInstall";
    fixup = "fixup";
    build-tools = "buildTools";
  };

  refuse =
    where: package: table: known:
    let
      unknown = builtins.attrNames (removeAttrs table known);
    in
    if unknown == [ ] then
      table
    else
      throw (
        "${where} of ${projectName} gives '${package}' the unknown keys "
        + "${builtins.concatStringsSep ", " unknown}. The keys are "
        + "${builtins.concatStringsSep ", " known}."
      );

  attributeOf =
    package: name:
    let
      parts = lib.splitString "." name;
    in
    if lib.hasAttrByPath parts pkgs then
      lib.getAttrFromPath parts pkgs
    else
      throw (
        "[tool.pydd.overrides.${package}] of ${projectName} names the build tool "
        + "'${name}', and nixpkgs holds no attribute of that name."
      );

  fromToml =
    package: table:
    lib.mapAttrs' (
      key: value:
      lib.nameValuePair keys.${key} (
        if key == "patches" then
          map (path: project + "/${path}") value
        else if key == "build-tools" then
          map (attributeOf package) value
        else
          value
      )
    ) (refuse "[tool.pydd.overrides]" package table (builtins.attrNames keys));

  fromArgument =
    package: table: refuse "the overrides argument" package table (builtins.attrValues keys);

  packages = lib.unique (builtins.attrNames declared ++ builtins.attrNames overrides);

  merged = lib.genAttrs packages (
    package:
    lib.optionalAttrs (declared ? ${package}) (fromToml package declared.${package})
    // lib.optionalAttrs (overrides ? ${package}) (fromArgument package overrides.${package})
  );
in
{
  /** The build tools that an override names, by package. */
  buildTools = lib.mapAttrs (_: override: override.buildTools) (
    lib.filterAttrs (_: override: override ? buildTools) merged
  );

  /** Every other key, by package, for the `overrides` argument of `planner.nix`. */
  nodes = lib.filterAttrs (_: override: override != { }) (
    lib.mapAttrs (_: override: removeAttrs override [ "buildTools" ]) merged
  );
}
