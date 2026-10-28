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
#     [tool.pydd.overrides.rich]
#     dependencies = { add = ["typing-extensions"], drop = ["pygments"] }
#     artifact = { url = "https://example.org/rich-15.0.0-py3-none-any.whl", hash = "sha256-..." }
#
#     overrides.numpy = { configSettings = { ... }; patches = [ ./x.patch ]; };
#
# A patch in `pyproject.toml` is a path relative to the project. A build tool
# there names an attribute of `pkgs`, and a dot walks into it.
#
# Two keys change the resolution, and not the node. `dependencies` adds and
# drops edges of the package in the walk of the lock; an added name must be in
# the lock already. `artifact` replaces every artefact of the package in the
# menu with one file: `{ url, hash }`, a path (relative to the project in
# `pyproject.toml`), or a derivation. The file keeps the version of the lock.
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
    dependencies = "dependencies";
    artifact = "artifact";
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
        else if key == "artifact" && builtins.isString value then
          project + "/${value}"
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
  /**
    The build tools that an override names, by package.
  */
  buildTools = lib.mapAttrs (_: override: override.buildTools) (
    lib.filterAttrs (_: override: override ? buildTools) merged
  );

  /**
    The edges that an override adds and drops, by package, as `{ add, drop }`.
  */
  dependencies = lib.mapAttrs (_: override: override.dependencies) (
    lib.filterAttrs (_: override: override ? dependencies) merged
  );

  /**
    The one artefact that replaces the artefacts of each package in the menu.
  */
  artifacts = lib.mapAttrs (_: override: override.artifact) (
    lib.filterAttrs (_: override: override ? artifact) merged
  );

  /**
    Every other key, by package, for the `overrides` argument of `planner.nix`.
  */
  nodes = lib.filterAttrs (_: override: override != { }) (
    lib.mapAttrs (
      _: override:
      removeAttrs override [
        "buildTools"
        "dependencies"
        "artifact"
      ]
    ) merged
  );
}
